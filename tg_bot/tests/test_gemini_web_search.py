import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from google.genai import types
from google.genai.chats import AsyncChats

from core.logger import logger
from tg_bot.services.gemini_tools import MAX_TOOL_CALLS, build_gemini_config, run_gemini_turn
from tg_bot.services.web_search import MAX_QUERY_LENGTH, MAX_RESULTS, WebSearchService


def response(text=None, calls=(), signature=None):
    parts = [types.Part(function_call=call, thought_signature=signature) for call in calls]
    if text is not None:
        parts.append(types.Part.from_text(text=text))
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=parts), finish_reason=types.FinishReason.STOP)
        ]
    )


def search_call(query="latest Python release", call_id="call-1"):
    return types.FunctionCall(name="web_search", args={"query": query}, id=call_id)


def sdk_chat(responses):
    modules = MagicMock()
    modules.generate_content = AsyncMock(side_effect=responses)
    return AsyncChats(modules).create(model="configured-model"), modules


def search_service(handler):
    return WebSearchService("test-serper-key", transport=httpx.MockTransport(handler))


def successful_search(request):
    assert request.method == "POST"
    assert str(request.url) == "https://google.serper.dev/search"
    assert request.headers["X-API-KEY"] == "test-serper-key"
    assert "q" in json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "organic": [
                {"title": "Python release", "link": "https://www.python.org/downloads/", "snippet": "A current release"}
            ],
            "ignoredSecret": "raw response must not reach Gemini",
        },
    )


@pytest.mark.asyncio
async def test_plain_question_does_not_search():
    service = search_service(lambda request: pytest.fail("Unexpected Serper request"))
    chat, modules = sdk_chat([response("4")])
    assert await run_gemini_turn(chat, ["2+2"], service, "configured-model") == "4"
    config = modules.generate_content.call_args.kwargs["config"]
    assert config.tools[0].function_declarations[0].name == "web_search"
    assert config.tools[0].google_search is None
    assert config.automatic_function_calling.disable


@pytest.mark.asyncio
async def test_tool_results_sources_and_signature_return_in_sdk_history():
    service = search_service(successful_search)
    chat, modules = sdk_chat(
        [
            response(calls=[search_call()], signature=b"original-signature"),
            response("Current release [1].\n\nИсточники:\n[1] https://invented.invalid/release"),
        ]
    )
    text = await run_gemini_turn(chat, ["Question needing external evidence"], service, "configured-model")
    assert text == "Current release."
    assert "Источники" not in text and "https://" not in text and "[1]" not in text
    contents = modules.generate_content.call_args.kwargs["contents"]
    assert contents[1].parts[0].thought_signature == b"original-signature"
    tool_result = contents[2].parts[0].function_response
    assert tool_result.id == "call-1" and tool_result.name == "web_search"
    assert tool_result.response["results"][0]["url"] == "https://www.python.org/downloads/"
    assert tool_result.response["results"][0]["snippet"] == "A current release"
    assert "ignoredSecret" not in json.dumps(tool_result.response)
    assert len(chat.get_history()) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500])
async def test_http_search_failure_is_returned_and_explicitly_disclosed(status):
    service = search_service(lambda request: httpx.Response(status, json={"error": "test"}))
    chat, modules = sdk_chat([response(calls=[search_call()]), response("Не могу проверить новую версию.")])
    text = await run_gemini_turn(chat, ["Latest?"], service, "model")
    assert "Свежие данные сейчас проверить не удалось" in text
    assert "Источники:" not in text
    contents = modules.generate_content.call_args.kwargs["contents"]
    assert str(status) in contents[-1].parts[0].function_response.response["error"]


@pytest.mark.asyncio
async def test_timeout_is_returned_to_model():
    def timeout(request):
        raise httpx.ReadTimeout("timeout with test-serper-key", request=request)

    service = search_service(timeout)
    chat, modules = sdk_chat([response(calls=[search_call()]), response("Нет свежих данных.")])
    text = await run_gemini_turn(chat, ["Question needing external evidence"], service, "model")
    assert "Свежие данные сейчас проверить не удалось" in text
    error = modules.generate_content.call_args.kwargs["contents"][-1].parts[0].function_response.response["error"]
    assert "timeout" in error and "test-serper-key" not in error


@pytest.mark.asyncio
async def test_repeated_queries_use_one_http_request_and_loop_is_bounded():
    requests = []

    def handler(request):
        requests.append(request)
        return successful_search(request)

    chat, modules = sdk_chat([response(calls=[search_call()])] * MAX_TOOL_CALLS + [response("Answer [1]")])
    text = await run_gemini_turn(chat, ["Latest?"], search_service(handler), "model")
    assert len(requests) == 1 and modules.generate_content.await_count == MAX_TOOL_CALLS + 1
    assert modules.generate_content.call_args.kwargs["config"].tool_config.function_calling_config.mode == "NONE"
    assert text == "Answer"
    assert "https://www.python.org/downloads/" not in text


@pytest.mark.asyncio
async def test_parallel_tool_calls_cannot_exceed_credit_limit():
    requests = []

    def handler(request):
        requests.append(request)
        return successful_search(request)

    chat, modules = sdk_chat(
        [response(calls=[search_call(f"query-{i}", str(i)) for i in range(6)]), response("Summary [1]")]
    )
    text = await run_gemini_turn(chat, ["News"], search_service(handler), "model")
    assert len(requests) == MAX_TOOL_CALLS
    tool_parts = modules.generate_content.call_args.kwargs["contents"][-1].parts
    assert len(tool_parts) == 6
    assert "limit" in tool_parts[-1].function_response.response["error"]
    assert "Свежие данные сейчас проверить не удалось" in text


@pytest.mark.asyncio
async def test_failed_query_is_not_retried_within_one_turn():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429)

    chat, _ = sdk_chat([response(calls=[search_call()])] * MAX_TOOL_CALLS + [response("Cannot verify")])
    await run_gemini_turn(chat, ["News"], search_service(handler), "model")
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_sdk_chat_keeps_previous_search_for_followup():
    chat, modules = sdk_chat(
        [response(calls=[search_call()]), response("Release [1]"), response("Compared with that release...")]
    )
    service = search_service(successful_search)
    await run_gemini_turn(chat, ["Latest release?"], service, "model")
    await run_gemini_turn(chat, ["How does it differ?"], service, "model")
    contents = modules.generate_content.call_args.kwargs["contents"]
    assert contents[0].parts[0].text == "Latest release?"
    assert contents[2].parts[0].function_response.response["results"][0]["url"] == "https://www.python.org/downloads/"
    assert contents[-1].parts[0].text == "How does it differ?"


@pytest.mark.asyncio
async def test_missing_search_key_does_not_break_ordinary_gemini():
    service = WebSearchService("")
    chat, _ = sdk_chat([response("Привет!")])
    assert await run_gemini_turn(chat, ["Привет"], service, "model") == "Привет!"
    chat, _ = sdk_chat([response(calls=[search_call()]), response("Не могу проверить.")])
    assert "Свежие данные сейчас проверить не удалось" in await run_gemini_turn(chat, ["News"], service, "model")


@pytest.mark.asyncio
async def test_cache_ttl_and_case_insensitive_reuse(monkeypatch):
    requests = []
    now = [100.0]
    monkeypatch.setattr("tg_bot.services.web_search.time.monotonic", lambda: now[0])

    def handler(request):
        requests.append(request)
        return successful_search(request)

    service = search_service(handler)
    first = await service.search("latest Python")
    assert await service.search(" latest  PYTHON ") == first
    assert len(requests) == 1
    now[0] = 401.0
    await service.search("latest Python")
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_api_key_not_in_logs_even_when_exception_or_query_contains_it():
    messages = []
    sink = logger.add(lambda message: messages.append(str(message)))
    try:

        def handler(request):
            raise httpx.ConnectError("test-serper-key", request=request)

        await search_service(handler).search("query test-serper-key")
        logger.complete()
    finally:
        logger.remove(sink)
    assert "test-serper-key" not in "".join(messages)
    assert "[REDACTED]" in "".join(messages)


@pytest.mark.asyncio
async def test_query_length_validation_does_not_use_credits():
    service = search_service(lambda request: pytest.fail("Invalid query spent credits"))
    assert (await service.search("x" * (MAX_QUERY_LENGTH + 1))).error
    assert (await service.search(" ")).error


@pytest.mark.asyncio
async def test_answerbox_and_result_limit_and_unsafe_links():
    organic = [{"title": str(i), "link": f"https://example{i}.org/{i}", "snippet": "evidence"} for i in range(20)]
    organic.insert(0, {"title": "unsafe", "link": "javascript:alert(1)", "snippet": "unsafe"})
    service = search_service(
        lambda request: httpx.Response(
            200,
            json={
                "organic": organic,
                "answerBox": {"title": "Answer", "link": "https://answer.example/", "answer": "Useful answer"},
            },
        )
    )
    result = await service.search("query")
    assert len(result.sources) == MAX_RESULTS
    assert result.sources[0].snippet == "Useful answer"
    assert all(source.link.startswith("https://") for source in result.sources)


def test_context_has_dynamic_date_identity_and_no_google_grounding():
    config = build_gemini_config("configured-model", "Custom instruction")
    assert isinstance(config.system_instruction, str)
    assert f"Current date: {datetime.now().astimezone().date().isoformat()}" in config.system_instruction
    assert "Provider: Google Gemini API" in config.system_instruction
    assert "Model: configured-model" in config.system_instruction
    assert "Custom instruction" in config.system_instruction
    assert "knowledge cutoff" in config.system_instruction
    assert config.response_modalities == ["TEXT"]
    assert config.tools and isinstance(config.tools[0], types.Tool)
    assert config.tools[0].google_search is None


@pytest.mark.asyncio
@pytest.mark.parametrize("use_search", [False, True])
@pytest.mark.parametrize("kind", ["ask", "chat"])
@pytest.mark.parametrize("mime", ["image/jpeg", "application/pdf", "audio/mpeg", "video/mp4", "text/plain"])
async def test_existing_file_api_upload_active_wait_and_tool_call(monkeypatch, tmp_path, kind, mime, use_search):
    from tg_bot.services import gpt

    path = tmp_path / "attachment"
    path.write_bytes(b"mock file data")
    pending = types.File(
        name="files/mock", uri="https://files.example/mock", mime_type=mime, state=types.FileState.PROCESSING
    )
    active = pending.model_copy(update={"state": types.FileState.ACTIVE})
    modules = MagicMock()
    responses = (
        [response(calls=[search_call()]), response("File analysis [1]")] if use_search else [response("File analysis")]
    )
    modules.generate_content = AsyncMock(side_effect=responses)
    client = MagicMock()
    client.aio.chats = AsyncChats(modules)
    client.aio.files.upload = AsyncMock(return_value=pending)
    client.aio.files.get = AsyncMock(return_value=active)
    monkeypatch.setattr(gpt.genai, "Client", lambda **kwargs: client)
    monkeypatch.setattr(gpt.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(gpt, "WEB_SEARCH", search_service(successful_search))
    model: gpt.GeminiModel | gpt.GeminiChatModel
    if kind == "ask":
        model = gpt.GeminiModel("mock-gemini-key", "test-model")
    else:
        model = gpt.GeminiChatModel("mock-gemini-key", "test-model")
        model.new_chat("Custom system")
    model.add_file(gpt.GeminiFile(path, mime))
    text = await (
        model.get_response("Analyze current facts")
        if isinstance(model, gpt.GeminiModel)
        else model.send_message("Analyze current facts")
    )
    assert text == "File analysis"
    assert "Источники" not in text and "https://" not in text and "[1]" not in text
    client.aio.files.upload.assert_awaited_once()
    assert client.aio.files.upload.call_args.kwargs["config"].mime_type == mime
    client.aio.files.get.assert_awaited_once_with(name="files/mock")
    contents = modules.generate_content.call_args.kwargs["contents"]
    assert any(part.file_data and part.file_data.file_uri == active.uri for part in contents[0].parts)
    assert not path.exists() and not model.files_to_upload


@pytest.mark.asyncio
async def test_chat_system_date_refreshes_on_next_message(monkeypatch):
    clock = MagicMock()
    clock.now.side_effect = [datetime(2026, 1, 1), datetime(2026, 1, 2)]
    monkeypatch.setattr("tg_bot.services.gemini_tools.datetime", clock)
    chat, modules = sdk_chat([response("First"), response("Second")])
    service = search_service(lambda request: pytest.fail("Unexpected search"))
    await run_gemini_turn(chat, ["Hello"], service, "model")
    await run_gemini_turn(chat, ["Hello again"], service, "model")
    instructions = [call.kwargs["config"].system_instruction for call in modules.generate_content.call_args_list]
    assert "Current date: 2026-01-01" in instructions[0]
    assert "Current date: 2026-01-02" in instructions[1]


@pytest.mark.asyncio
async def test_empty_search_results_are_disclosed():
    service = search_service(lambda request: httpx.Response(200, json={"organic": []}))
    chat, modules = sdk_chat([response(calls=[search_call()]), response("No evidence")])
    text = await run_gemini_turn(chat, ["Latest?"], service, "model")
    assert "Свежие данные сейчас проверить не удалось" in text
    assert "Источники:" not in text
    assert (
        "No results"
        in modules.generate_content.call_args.kwargs["contents"][-1].parts[0].function_response.response["error"]
    )


@pytest.mark.asyncio
async def test_search_urls_and_markdown_links_are_internal_only():
    chat, _ = sdk_chat(
        [
            response(calls=[search_call()]),
            response(
                "Актуальные данные [1], [2].\nПодробнее: [документация](https://www.python.org/downloads/)\nhttps://invented.invalid/ www.example.org"
            ),
        ]
    )
    text = await run_gemini_turn(chat, ["Latest?"], search_service(successful_search), "model")
    assert "Актуальные данные" in text and "документация" in text
    assert "https://" not in text and "www." not in text
    assert "[1]" not in text and "[2]" not in text and "Источники" not in text


@pytest.mark.asyncio
async def test_natural_search_answer_is_not_changed_or_given_sources():
    chat, _ = sdk_chat([response(calls=[search_call()]), response("Доступна новая версия Python.")])
    assert (
        await run_gemini_turn(chat, ["Latest?"], search_service(successful_search), "model")
        == "Доступна новая версия Python."
    )


@pytest.mark.asyncio
async def test_answerbox_is_in_internal_context_with_url_and_snippet():
    service = search_service(
        lambda request: httpx.Response(
            200,
            json={
                "answerBox": {
                    "title": "Official answer",
                    "link": "https://official.example/",
                    "answer": "Verified fact",
                },
                "organic": [],
            },
        )
    )
    chat, modules = sdk_chat([response(calls=[search_call()]), response("Verified fact")])
    text = await run_gemini_turn(chat, ["Question"], service, "model")
    result = modules.generate_content.call_args.kwargs["contents"][-1].parts[0].function_response.response["results"][0]
    assert result == {"title": "Official answer", "url": "https://official.example/", "snippet": "Verified fact"}
    assert text == "Verified fact"


def test_search_instructions_require_natural_answers_and_primary_sources():
    config = build_gemini_config("model")
    assert isinstance(config.system_instruction, str)
    assert "Do not list sources, insert URLs or citation numbers" in config.system_instruction
    assert "Prefer official sites and primary sources" in config.system_instruction
    assert "state uncertainty rather than guess" in config.system_instruction
    assert "YouTube, Reddit or random" in config.system_instruction
