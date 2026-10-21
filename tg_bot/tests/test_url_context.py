import pytest
from google.genai import types
from google.genai.errors import ClientError

from core.logger import logger
from tg_bot.services import gemini_tools
from tg_bot.services.gemini_tools import run_gemini_turn
from tg_bot.services.url_context import MAX_URLS, extract_urls, is_public_url
from tg_bot.tests.test_gemini_web_search import response, sdk_chat, search_call, search_service, successful_search


def url_response(text, urls, statuses=None, calls=()):
    result = response(text, calls=calls)
    result.candidates[0].url_context_metadata = types.UrlContextMetadata(
        url_metadata=[
            types.UrlMetadata(retrieved_url=url, url_retrieval_status=status)
            for url, status in zip(
                urls, statuses or [types.UrlRetrievalStatus.URL_RETRIEVAL_STATUS_SUCCESS] * len(urls), strict=True
            )
        ]
    )
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("urls", [["https://example.com/article"], ["https://example.com/a", "https://example.com/b"]])
async def test_urls_enable_validated_combination_and_preserve_current_message(urls):
    chat, modules = sdk_chat([url_response("Verified page content", urls)])
    message = "Read " + " and ".join(urls)
    answer = await run_gemini_turn(
        chat, [message], search_service(lambda request: pytest.fail("No search needed")), "gemini-3.5-flash-lite"
    )
    config = modules.generate_content.call_args.kwargs["config"]
    assert any(tool.url_context for tool in config.tools)
    assert any(tool.function_declarations for tool in config.tools)
    assert config.tool_config.include_server_side_tool_invocations is True
    assert config.tool_config.function_calling_config.mode == "VALIDATED"
    assert modules.generate_content.call_args.kwargs["contents"][0].parts[0].text == message
    assert answer == "Verified page content"
    assert len(chat.get_history()) == 2


@pytest.mark.asyncio
async def test_quoted_url_is_used_without_scanning_random_history():
    url = "https://example.com/article"
    chat, modules = sdk_chat([response("Old"), url_response("Article", [url]), response("Ordinary follow-up")])
    service = search_service(lambda request: pytest.fail("No Serper called by mock"))
    await run_gemini_turn(chat, ["Old https://unrelated.example/"], service, "model")
    text = f"Контекст из предыдущего сообщения:\n---\n{url}\n---\n\nСравни характеристики"
    assert await run_gemini_turn(chat, [text], service, "model") == "Article"
    config = modules.generate_content.call_args.kwargs["config"]
    assert url in config.system_instruction and "unrelated.example" not in config.system_instruction
    await run_gemini_turn(chat, ["Спасибо"], service, "model")
    assert all(tool.url_context is None for tool in modules.generate_content.call_args.kwargs["config"].tools)


@pytest.mark.asyncio
async def test_serper_and_server_tool_parts_signatures_ids_survive_two_rounds_and_followup():
    url = "https://example.com/article?token=private-value"
    first = url_response("", [url], calls=[search_call("Verify current release")])
    first.candidates[0].content.parts.insert(
        0,
        types.Part(
            tool_call=types.ToolCall(id="url-call", tool_type=types.ToolType.URL_CONTEXT, args={"urls": [url]}),
            thought_signature=b"server-call",
        ),
    )
    first.candidates[0].content.parts.insert(
        1,
        types.Part(
            tool_response=types.ToolResponse(
                id="url-call",
                tool_type=types.ToolType.URL_CONTEXT,
                response={
                    "url_metadata": [{"retrieved_url": url, "url_retrieval_status": "URL_RETRIEVAL_STATUS_SUCCESS"}]
                },
            ),
            thought_signature=b"server-response",
        ),
    )
    first.candidates[0].content.parts[-1].thought_signature = b"custom-call"
    chat, modules = sdk_chat(
        [first, response(f"Page {url} verified [1] https://unrequested.example/"), response("Follow-up")]
    )
    service = search_service(successful_search)
    text = await run_gemini_turn(chat, [f"Read {url} and check freshness"], service, "gemini-3.5-flash-lite")
    assert url in text and "unrequested.example" not in text
    contents = modules.generate_content.call_args.kwargs["contents"]
    parts = contents[1].parts
    assert parts[0].thought_signature == b"server-call" and parts[1].thought_signature == b"server-response"
    assert parts[-1].thought_signature == b"custom-call"
    assert contents[2].parts[0].function_response.id == "call-1"
    assert sum(content.parts[0].text == f"Read {url} and check freshness" for content in contents) == 1
    await run_gemini_turn(chat, ["а что изменилось?"], service, "gemini-3.5-flash-lite")
    final = modules.generate_content.call_args.kwargs
    assert any(part.tool_response for content in final["contents"] for part in content.parts)
    assert final["config"].tool_config.include_server_side_tool_invocations is True
    assert all(tool.url_context is None for tool in final["config"].tools)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", list(types.UrlRetrievalStatus))
async def test_retrieval_status_must_confirm_success(status):
    url = "https://example.com/a"
    chat, _ = sdk_chat([url_response("Invented page analysis", [url], [status])])
    answer = await run_gemini_turn(
        chat, [f"Read {url}"], search_service(lambda request: pytest.fail("No Serper")), "model"
    )
    assert ("Invented page analysis" in answer) == (status == types.UrlRetrievalStatus.URL_RETRIEVAL_STATUS_SUCCESS)
    if status != types.UrlRetrievalStatus.URL_RETRIEVAL_STATUS_SUCCESS:
        assert url in answer and "Не прочитана" in answer


@pytest.mark.asyncio
async def test_missing_metadata_and_partial_retrieval_do_not_expose_invented_comparison():
    urls = ["https://example.com/a", "https://example.com/b"]
    for result in [response("Invented"), url_response("Invented", urls[:1])]:
        chat, _ = sdk_chat([result])
        answer = await run_gemini_turn(
            chat, ["Compare " + " ".join(urls)], search_service(lambda request: pytest.fail("No search")), "model"
        )
        assert "Invented" not in answer and "Не прочитана https://example.com/b" in answer


@pytest.mark.asyncio
async def test_disabled_flag_preserves_legacy_tool_route(monkeypatch):
    monkeypatch.setattr(gemini_tools, "GEMINI_URL_CONTEXT_ENABLED", False)
    chat, modules = sdk_chat([response("Legacy response")])
    assert (
        await run_gemini_turn(chat, ["Read https://example.com/article"], search_service(successful_search), "model")
        == "Legacy response"
    )
    config = modules.generate_content.call_args.kwargs["config"]
    assert all(tool.url_context is None for tool in config.tools)
    assert config.tool_config.function_calling_config.mode == "AUTO"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/a",
        "https://127.0.0.1/",
        "http://10.0.0.1/",
        "http://[::1]/",
        "https://169.254.169.254/",
        "file:///etc/passwd",
        "ftp://example.com/file",
        "https://user:pass@example.com/",
        "https://internal/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://127.0.0.1.nip.io/",
        "https://example.com:8443/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fe80::1%25eth0]/",
    ],
)
def test_private_and_unsupported_urls_are_rejected(url):
    assert not is_public_url(url)


@pytest.mark.asyncio
async def test_invalid_url_and_over_limit_never_generate_and_are_recorded_in_history():
    for text in ["Read http://127.0.0.1/", "Read " + " ".join(f"https://example.com/{i}" for i in range(MAX_URLS + 1))]:
        chat, modules = sdk_chat([])
        answer = await run_gemini_turn(chat, [text], search_service(lambda request: pytest.fail("No calls")), "model")
        assert "20" in answer
        modules.generate_content.assert_not_awaited()
        assert len(chat.get_history()) == 2


@pytest.mark.asyncio
async def test_multimedia_parts_are_preserved_in_url_request():
    url = "https://example.com/a"
    chat, modules = sdk_chat([url_response("Verified image and page", [url])])
    parts: list[types.PartUnionDict] = [
        types.Part.from_text(text=f"Compare media and {url}"),
        types.Part.from_uri(file_uri="https://files.example/1", mime_type="image/jpeg"),
    ]
    await run_gemini_turn(chat, parts, search_service(successful_search), "model")
    assert (
        modules.generate_content.call_args.kwargs["contents"][0].parts[1].file_data.file_uri
        == "https://files.example/1"
    )


@pytest.mark.asyncio
async def test_url_api_failure_is_controlled_and_does_not_leak_error_payload():
    chat, modules = sdk_chat([])
    modules.generate_content.side_effect = ClientError(
        400, {"error": {"message": "private token payload", "code": 400}}
    )
    answer = await run_gemini_turn(chat, ["Read https://example.com/a"], search_service(successful_search), "model")
    assert "private token payload" not in answer and "Не прочитана" in answer


@pytest.mark.asyncio
async def test_query_tokens_are_not_logged():
    logs = []
    sink = logger.add(lambda message: logs.append(str(message)))
    url = "https://example.com/article?access_token=private-token#private-fragment"
    try:
        chat, _ = sdk_chat([url_response("", [url], calls=[search_call(url)]), response("Verified")])
        await run_gemini_turn(chat, [f"Read {url}"], search_service(successful_search), "model")
        logger.complete()
    finally:
        logger.remove(sink)
    assert "private-token" not in "".join(logs) and "private-fragment" not in "".join(logs)


def test_markdown_urls_are_extracted_and_deduplicated_without_changing_url():
    url = "https://example.com/a_(b)?q=test"
    assert extract_urls(f"Read [article]({url}) and {url}.") == [url]


@pytest.mark.asyncio
async def test_requested_url_is_not_removed_from_source_section_after_serper():
    url = "https://example.com/article"
    chat, _ = sdk_chat(
        [
            url_response("", [url], calls=[search_call()]),
            response(f"Analysis\n\nИсточники:\n{url}\nhttps://unrequested.example/"),
        ]
    )
    answer = await run_gemini_turn(chat, [f"Read {url} and verify"], search_service(successful_search), "model")
    assert url in answer and "unrequested.example" not in answer
    assert "Источники" not in answer


@pytest.mark.asyncio
async def test_failed_page_analysis_is_retracted_in_history_before_followup():
    chat, modules = sdk_chat([response("Invented page contents"), response("No verified content")])
    service = search_service(successful_search)
    await run_gemini_turn(chat, ["Read https://example.com/a"], service, "model")
    await run_gemini_turn(chat, ["Что удалось узнать?"], service, "model")
    contents = modules.generate_content.call_args.kwargs["contents"]
    assert any(part.text and "was not verified" in part.text for content in contents for part in content.parts)


@pytest.mark.asyncio
async def test_actual_sdk_serializes_combined_tools_and_circulates_server_parts():
    import json

    import httpx
    from google import genai

    url = "https://example.com/article"
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            parts = [
                {
                    "toolCall": {"id": "url-call", "toolType": "URL_CONTEXT", "args": {"urls": [url]}},
                    "thoughtSignature": "YQ==",
                },
                {
                    "toolResponse": {
                        "id": "url-call",
                        "toolType": "URL_CONTEXT",
                        "response": {
                            "url_metadata": [
                                {"retrieved_url": url, "url_retrieval_status": "URL_RETRIEVAL_STATUS_SUCCESS"}
                            ]
                        },
                    },
                    "thoughtSignature": "Yg==",
                },
                {
                    "functionCall": {
                        "name": "web_search",
                        "id": "search-call",
                        "args": {"query": "verify article facts"},
                    },
                    "thoughtSignature": "Yw==",
                },
            ]
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": parts},
                            "finishReason": "STOP",
                            "urlContextMetadata": {
                                "urlMetadata": [
                                    {"retrievedUrl": url, "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"}
                                ]
                            },
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "Verified actual SDK response"}]},
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    client = genai.Client(
        api_key="mock-key",
        http_options=types.HttpOptions(
            client_args={"trust_env": False},
            async_client_args={"trust_env": False, "transport": httpx.MockTransport(handler)},
        ),
    )
    try:
        chat = client.aio.chats.create(model="gemini-3.5-flash-lite")
        assert (
            await run_gemini_turn(
                chat, [f"Read {url} and verify"], search_service(successful_search), "gemini-3.5-flash-lite"
            )
            == "Verified actual SDK response"
        )
        assert len(requests) == 2
        assert requests[0]["toolConfig"]["includeServerSideToolInvocations"] is True
        assert requests[0]["toolConfig"]["functionCallingConfig"]["mode"] == "VALIDATED"
        assert any("urlContext" in tool for tool in requests[0]["tools"])
        history = requests[1]["contents"]
        assert history[1]["parts"][0]["thoughtSignature"] == "YQ=="
        assert history[1]["parts"][1]["toolResponse"]["id"] == "url-call"
        assert history[2]["parts"][0]["functionResponse"]["id"] == "search-call"
    finally:
        await client.aio.aclose()
        client.close()


@pytest.mark.asyncio
async def test_combined_loop_exhaustion_keeps_validated_and_disables_serper_declarations():
    url = "https://example.com/a"
    first = url_response("", [url], calls=[search_call()])
    chat, modules = sdk_chat(
        [first, response(calls=[search_call()]), response(calls=[search_call()]), response("Final")]
    )
    calls = []

    def handler(request):
        calls.append(request)
        return successful_search(request)

    assert await run_gemini_turn(chat, [f"Read {url} and verify"], search_service(handler), "model") == "Final"
    assert len(calls) == 1
    config = modules.generate_content.call_args.kwargs["config"]
    assert config.tool_config.function_calling_config.mode == "VALIDATED"
    assert config.tool_config.include_server_side_tool_invocations is True
    assert all(not tool.function_declarations for tool in config.tools)
