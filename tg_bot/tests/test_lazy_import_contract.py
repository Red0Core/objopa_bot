"""Focused static regression tests for PR17 lazy-loading architecture."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def tree(relative):
    return ast.parse((ROOT / relative).read_text(encoding='utf-8'), filename=relative)


def top_imports(relative):
    module = tree(relative)
    return [node for node in module.body if isinstance(node, (ast.Import, ast.ImportFrom))]


def module_import_names(relative):
    names = []
    for node in top_imports(relative):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        else:
            names.append(node.module or '')
    return names


def function(tree_, name):
    matches = [n for n in ast.walk(tree_) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert len(matches) == 1
    return matches[0]


def test_gpt_module_does_not_import_heavy_sdk_on_router_registration():
    names = module_import_names('tg_bot/services/gpt.py')
    assert not any(n.startswith(('google', 'openai', 'httpx', 'tg_bot.services.gemini_tools')) for n in names)
    t = tree('tg_bot/services/gpt.py')
    loader = function(t, '_load_gemini_runtime')
    assert any(isinstance(x, ast.ImportFrom) and x.module == 'google' for x in ast.walk(loader))
    classes = {node.name: node for node in t.body if isinstance(node, ast.ClassDef)}
    for cls in ('GeminiModel','GeminiChatModel'):
        init = function(classes[cls], '__init__')
        assert any(isinstance(x,ast.Call) and isinstance(x.func,ast.Name) and x.func.id=='_load_gemini_runtime' for x in ast.walk(init))


def test_dice_does_not_instantiate_gemini_during_import():
    t = tree('tg_bot/routers/mention_dice.py')
    assert not any(isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='GeminiModel' for n in t.body)
    get_client = function(t, 'get_ai_client')
    assert any(isinstance(x,ast.Call) and isinstance(x.func,ast.Name) and x.func.id=='GeminiModel' for x in ast.walk(get_client))
    tracker = (ROOT/'tg_bot/routers/day_tracker.py').read_text(encoding='utf-8')
    assert 'AI_CLIENT' not in tracker
    assert tracker.count('get_ai_client().get_response(') == 2


def test_worker_registry_is_lazy_and_preserves_all_pipeline_names():
    t = tree('workers/queue_listener.py')
    names = module_import_names('workers/queue_listener.py')
    assert not any(x.startswith('workers.') and x.endswith('_pipeline') for x in names)
    registry = next(n.value for n in t.body if isinstance(n,ast.AnnAssign) and getattr(n.target,'id',None)=='PIPELINE_TYPE_REGISTRY')
    specs = dict(zip([k.value for k in registry.keys],[v.value for v in registry.values]))
    assert set(specs) == {'video_generation','image_generation','animation_generation','delete_image_folder','concat_animations','reset_worker_session','set_animations_force'}
    assert all(v.startswith('workers.') and ':' in v for v in specs.values())
    process = function(t,'process_task')
    assert any(isinstance(x,ast.Call) and isinstance(x.func,ast.Name) and x.func.id=='import_module' for x in ast.walk(process))


def test_web_downloader_untouched_and_scheduler_html_deferred():
    names = module_import_names('tg_bot/tasks/sheduled.py')
    assert 'httpcloak' not in names and 'lxml' not in names
    f = function(tree('tg_bot/tasks/sheduled.py'),'get_stream_promotion_prices')
    body_imports = [n for n in ast.walk(f) if isinstance(n,(ast.Import,ast.ImportFrom))]
    assert any(isinstance(n, ast.Import) and n.names[0].name == 'httpcloak' for n in body_imports)
    assert any(isinstance(n, ast.ImportFrom) and n.module == 'lxml' for n in body_imports)
