import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Viewport:
  width: int
  height: int


@dataclass(frozen=True)
class Timeouts:
  navigation: int
  element: int


@dataclass(frozen=True)
class VideoPolicy:
  record: bool
  embed_policy: str
  embed_bytes_limit: int


@dataclass(frozen=True)
class BrowserConfig:
  session: str | None
  headed: bool
  display: str | None
  reuse_session: bool
  persistent: bool
  profile: str | None
  pause_after_open: bool


@dataclass(frozen=True)
class AuthConfig:
  state_load_path: str | None
  state_save_path: str | None


@dataclass(frozen=True)
class Story:
  story_id: str
  name: str
  criteria: list[str]
  selectors: dict[str, str]
  steps: list[dict[str, Any]]
  path: Path


_TEMPLATE_RE = re.compile(r"\$\{([A-Za-z0-9_-]+)\}")

_SUPPORTED_STEPS = {
  'back',
  'check',
  'click',
  'evaluate',
  'expectAttrEquals',
  'expectCount',
  'expectDisabled',
  'expectEnabled',
  'expectEval',
  'expectTextEquals',
  'expectUrlContains',
  'expectVisible',
  'expectTextContains',
  'fill',
  'forward',
  'goto',
  'reload',
  'screenshot',
  'select',
  'sleepMs',
  'type',
  'uncheck',
  'waitFor',
  'waitForUrl'
}


def _now_run_id() -> str:
  return time.strftime('%Y-%m-%dT%H-%M-%SZ', time.gmtime())


def _escape(value: str) -> str:
  return (
    str(value)
    .replace('&', '&amp;')
    .replace('<', '&lt;')
    .replace('>', '&gt;')
    .replace('"', '&quot;')
    .replace("'", '&#039;')
  )


def _b64(path: Path) -> str:
  return base64.b64encode(path.read_bytes()).decode('ascii')


def _embed_png(path: Path) -> str:
  return f'data:image/png;base64,{_b64(path)}'


def _embed_webm(path: Path) -> str:
  return f'data:video/webm;base64,{_b64(path)}'


def _ensure_url_ok(url: str) -> None:
  with urllib.request.urlopen(url, timeout=10) as resp:
    if resp.status < 200 or resp.status >= 400:
      raise RuntimeError(f'base url not reachable: {url} (status {resp.status})')


def _wait_for_url(url: str, timeout_s: int) -> None:
  deadline = time.monotonic() + timeout_s
  last_error: Exception | None = None
  while time.monotonic() < deadline:
    try:
      _ensure_url_ok(url)
      return
    except Exception as exc:
      last_error = exc
      time.sleep(0.25)
  raise RuntimeError(f'base url not reachable: {url} ({last_error})')


def _run(
  args: list[str],
  *,
  timeout_s: int | None = None,
  env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
  return subprocess.run(args, text=True, capture_output=True, timeout=timeout_s, env=env)


def _parse_playwright_result(stdout: str) -> dict:
  lines = stdout.splitlines()
  for index, line in enumerate(lines):
    if line.strip() == '### Result':
      for j in range(index + 1, min(index + 8, len(lines))):
        candidate = lines[j].strip()
        if candidate.startswith('{'):
          return json.loads(candidate)
  raise RuntimeError('missing ### Result json')


def _resolve_path(base_dir: Path, value: str | None) -> str | None:
  if not value:
    return None
  path = Path(value)
  if path.is_absolute():
    return str(path)
  return str((base_dir / path).resolve())


def _load_config(config_path: Path) -> tuple[str, Viewport, Timeouts, VideoPolicy, BrowserConfig, AuthConfig]:
  cfg = json.loads(config_path.read_text(encoding='utf-8'))

  browser_cfg = cfg.get('browser') or {}
  auth_cfg = cfg.get('auth') or {}
  viewport_cfg = cfg.get('viewport') or {}
  timeouts_cfg = cfg.get('timeoutsMs') or {}
  video_cfg = cfg.get('video') or {}

  base_url = str(cfg.get('baseUrl') or '').strip()
  if not base_url:
    raise RuntimeError('config.baseUrl is required')

  viewport = Viewport(width=int(viewport_cfg.get('width', 1280)), height=int(viewport_cfg.get('height', 720)))
  timeouts = Timeouts(navigation=int(timeouts_cfg.get('navigation', 15000)), element=int(timeouts_cfg.get('element', 15000)))
  video = VideoPolicy(
    record=bool(video_cfg.get('record', True)),
    embed_policy=str(video_cfg.get('embedPolicy', 'always')),
    embed_bytes_limit=int(video_cfg.get('embedBytesLimit', 5_000_000))
  )

  if video.embed_policy not in {'always', 'on-fail', 'never'}:
    raise RuntimeError('config.video.embedPolicy must be one of: always, on-fail, never')

  browser = BrowserConfig(
    session=str(browser_cfg.get('session') or '').strip() or None,
    headed=bool(browser_cfg.get('headed', False)),
    display=(str(browser_cfg.get('display')).strip() if browser_cfg.get('display') is not None else None),
    reuse_session=bool(browser_cfg.get('reuseSession', False)),
    persistent=bool(browser_cfg.get('persistent', False)),
    profile=_resolve_path(config_path.parent, str(browser_cfg.get('profile') or '').strip() or None),
    pause_after_open=bool(browser_cfg.get('pauseAfterOpen', False))
  )

  auth = AuthConfig(
    state_load_path=_resolve_path(config_path.parent, str(auth_cfg.get('stateLoadPath') or '').strip() or None),
    state_save_path=_resolve_path(config_path.parent, str(auth_cfg.get('stateSavePath') or '').strip() or None)
  )

  return base_url, viewport, timeouts, video, browser, auth


def _load_stories(stories_dir: Path) -> list[Story]:
  story_files = sorted([*stories_dir.glob('*.yaml'), *stories_dir.glob('*.yml')])
  if len(story_files) == 0:
    raise RuntimeError(f'no story yaml files found in {stories_dir}')

  stories: list[Story] = []

  for path in story_files:
    raw = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict):
      raise RuntimeError(f'invalid story yaml: {path}')

    story_id = str(raw.get('id') or '').strip()
    name = str(raw.get('name') or '').strip()
    criteria_raw = raw.get('criteria') or []
    selectors_raw = raw.get('selectors') or {}
    steps_raw = raw.get('steps') or []

    if not story_id or not name:
      raise RuntimeError(f'story requires id and name: {path}')
    if not isinstance(criteria_raw, list) or not all(isinstance(item, str) for item in criteria_raw):
      raise RuntimeError(f'story.criteria must be a string list: {path}')
    if not isinstance(selectors_raw, dict) or not all(isinstance(k, str) for k in selectors_raw.keys()):
      raise RuntimeError(f'story.selectors must be a string map: {path}')
    # Normalize bare-string steps for no-payload kinds. Authors naturally
    # write `- reload` instead of `- reload: ~` or `- reload: {}`; YAML
    # parses that as a plain string. Promote it to a single-key dict so
    # the validator can dispatch normally. Restricted to the known
    # no-payload kinds so typos (e.g. `- reolad`) still fail loudly.
    if isinstance(steps_raw, list):
      _NO_PAYLOAD_KINDS = {'reload', 'back', 'forward'}
      steps_raw = [
        {item: None} if isinstance(item, str) and item in _NO_PAYLOAD_KINDS else item
        for item in steps_raw
      ]
    if not isinstance(steps_raw, list) or not all(isinstance(item, dict) for item in steps_raw):
      raise RuntimeError(f'story.steps must be a list of maps: {path}')

    selectors = {str(k): str(v) for k, v in selectors_raw.items()}
    selectors = _resolve_selector_templates(selectors, path)

    steps = _validate_steps(story_id, path, steps_raw)

    stories.append(
      Story(
        story_id=story_id,
        name=name,
        criteria=list(criteria_raw),
        selectors=selectors,
        steps=steps,
        path=path
      )
    )

  story_ids = [s.story_id for s in stories]
  if len(set(story_ids)) != len(story_ids):
    raise RuntimeError('duplicate story ids found')

  return sorted(stories, key=lambda s: s.story_id)


def _resolve_selector_templates(selectors: dict[str, str], path: Path) -> dict[str, str]:
  resolved = dict(selectors)

  for _ in range(20):
    changed = False
    for key, value in resolved.items():
      def _replace(match: re.Match[str]) -> str:
        var = match.group(1)
        if var not in resolved:
          raise RuntimeError(f'unknown selector template ${{{var}}} in {path}')
        return resolved[var]

      new_value = _TEMPLATE_RE.sub(_replace, value)
      if new_value != value:
        resolved[key] = new_value
        changed = True
    if not changed:
      return resolved

  raise RuntimeError(f'unresolved selector templates in {path}')


def _validate_steps(story_id: str, path: Path, steps_raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
  steps: list[dict[str, Any]] = []

  for index, step in enumerate(steps_raw):
    if len(step.keys()) != 1:
      raise RuntimeError(f'{story_id} ({path}): step[{index}] must have exactly one key')

    kind = next(iter(step.keys()))
    if kind not in _SUPPORTED_STEPS:
      raise RuntimeError(f'{story_id} ({path}): step[{index}] unknown kind {kind}')

    payload = step[kind]

    if kind == 'screenshot':
      if not isinstance(payload, dict) or 'key' not in payload:
        raise RuntimeError(f'{story_id} ({path}): step[{index}] screenshot requires key')
      steps.append(step)
      continue

    if kind in {'click', 'check', 'uncheck'}:
      if not isinstance(payload, str):
        raise RuntimeError(f'{story_id} ({path}): step[{index}] {kind} requires a string selector or selector key')
      steps.append(step)
      continue

    if kind in {'fill', 'select', 'type'}:
      if not isinstance(payload, dict) or 'sel' not in payload:
        raise RuntimeError(f'{story_id} ({path}): step[{index}] {kind} requires sel')
      if kind == 'fill' and 'value' not in payload:
        raise RuntimeError(f'{story_id} ({path}): step[{index}] fill requires value')
      if kind == 'select' and 'value' not in payload:
        raise RuntimeError(f'{story_id} ({path}): step[{index}] select requires value')
      if kind == 'type' and 'text' not in payload:
        raise RuntimeError(f'{story_id} ({path}): step[{index}] type requires text')
      steps.append(step)
      continue

    if kind == 'waitFor':
      if not isinstance(payload, dict) or 'sel' not in payload:
        raise RuntimeError(f'{story_id} ({path}): step[{index}] waitFor requires sel')
      state = payload.get('state')
      if state is not None and str(state) not in {'attached', 'detached', 'visible', 'hidden'}:
        raise RuntimeError(f'{story_id} ({path}): step[{index}] waitFor.state must be attached|detached|visible|hidden')
      steps.append(step)
      continue

    if kind == 'expectVisible':
      if not isinstance(payload, str):
        raise RuntimeError(f'{story_id} ({path}): step[{index}] expectVisible requires a string selector or selector key')
      steps.append(step)
      continue

    if kind == 'goto':
      if not isinstance(payload, str) or not payload.strip():
        raise RuntimeError(f'{story_id} ({path}): step[{index}] goto requires a string url (relative urls resolve against baseUrl)')
      steps.append(step)
      continue

    if kind in {'reload', 'back', 'forward'}:
      # These take no payload; accept null, empty string, or an empty dict.
      if payload not in (None, '', {}) and not (isinstance(payload, dict) and not payload):
        raise RuntimeError(f'{story_id} ({path}): step[{index}] {kind} takes no arguments')
      steps.append(step)
      continue

    if kind == 'evaluate':
      if not isinstance(payload, str) or not payload.strip():
        raise RuntimeError(f'{story_id} ({path}): step[{index}] evaluate requires a JS expression string')
      steps.append(step)
      continue

    if kind == 'waitForUrl':
      if not isinstance(payload, str) or not payload.strip():
        raise RuntimeError(f'{story_id} ({path}): step[{index}] waitForUrl requires a substring to wait for in page.url()')
      steps.append(step)
      continue

    if kind == 'expectUrlContains':
      if not isinstance(payload, str):
        raise RuntimeError(f'{story_id} ({path}): step[{index}] expectUrlContains requires a string')
      steps.append(step)
      continue

    if kind == 'expectCount':
      if not isinstance(payload, dict) or 'sel' not in payload or 'equals' not in payload:
        raise RuntimeError(f'{story_id} ({path}): step[{index}] expectCount requires sel and equals')
      if not isinstance(payload.get('equals'), int):
        raise RuntimeError(f'{story_id} ({path}): step[{index}] expectCount.equals must be an integer')
      steps.append(step)
      continue

    if kind == 'expectEval':
      if not isinstance(payload, dict) or 'expr' not in payload or 'equals' not in payload:
        raise RuntimeError(f'{story_id} ({path}): step[{index}] expectEval requires expr and equals')
      steps.append(step)
      continue

    if kind == 'expectTextEquals':
      if not isinstance(payload, dict) or 'sel' not in payload or 'equals' not in payload:
        raise RuntimeError(f'{story_id} ({path}): step[{index}] expectTextEquals requires sel and equals')
      steps.append(step)
      continue

    if kind == 'expectTextContains':
      if not isinstance(payload, dict) or 'sel' not in payload or 'contains' not in payload:
        raise RuntimeError(f'{story_id} ({path}): step[{index}] expectTextContains requires sel and contains')
      steps.append(step)
      continue

    if kind == 'expectAttrEquals':
      if not isinstance(payload, dict) or 'sel' not in payload or 'attr' not in payload or 'equals' not in payload:
        raise RuntimeError(f'{story_id} ({path}): step[{index}] expectAttrEquals requires sel, attr, equals')
      steps.append(step)
      continue

    if kind in {'expectEnabled', 'expectDisabled'}:
      if not isinstance(payload, str):
        raise RuntimeError(f'{story_id} ({path}): step[{index}] {kind} requires a string selector or selector key')
      steps.append(step)
      continue

    if kind == 'sleepMs':
      if not isinstance(payload, int):
        raise RuntimeError(f'{story_id} ({path}): step[{index}] sleepMs requires an integer')
      steps.append(step)
      continue

  return steps


_STEP_BOUNDARY = '⁣step_boundary⁣'


def _compile_steps(story: Story) -> str:
  lines: list[str] = []

  for index, step in enumerate(story.steps):
    lines.append(_STEP_BOUNDARY)

    kind = next(iter(step.keys()))
    payload = step[kind]

    if kind == 'screenshot':
      if not isinstance(payload, dict) or 'key' not in payload:
        raise RuntimeError(f'{story.story_id} ({story.path}): step[{index}] invalid screenshot')
      key = str(payload['key'])
      label = str(payload.get('label') or key)
      lines.append(f"await shot({json.dumps(key)}, {json.dumps(label)});")
      continue

    if kind == 'click':
      lines.append(f"await page.click(resolve({json.dumps(payload)}), {{ timeout: timeouts.element }});")
      continue

    if kind == 'fill':
      if not isinstance(payload, dict):
        raise RuntimeError(f'{story.story_id} ({story.path}): step[{index}] invalid fill')
      sel = payload.get('sel')
      value = payload.get('value')
      lines.append(
        f"await page.fill(resolve({json.dumps(sel)}), {json.dumps(value)}, {{ timeout: timeouts.element }});"
      )
      continue

    if kind == 'type':
      if not isinstance(payload, dict):
        raise RuntimeError(f'{story.story_id} ({story.path}): step[{index}] invalid type')
      sel = payload.get('sel')
      text = payload.get('text')
      lines.append(
        f"await page.locator(resolve({json.dumps(sel)})).type({json.dumps(text)}, {{ timeout: timeouts.element }});"
      )
      continue

    if kind == 'select':
      if not isinstance(payload, dict):
        raise RuntimeError(f'{story.story_id} ({story.path}): step[{index}] invalid select')
      sel = payload.get('sel')
      value = payload.get('value')
      lines.append(
        f"await page.selectOption(resolve({json.dumps(sel)}), {json.dumps(value)}, {{ timeout: timeouts.element }});"
      )
      continue

    if kind in {'check', 'uncheck'}:
      method = 'check' if kind == 'check' else 'uncheck'
      lines.append(f"await page.{method}(resolve({json.dumps(payload)}), {{ timeout: timeouts.element }});")
      continue

    if kind == 'waitFor':
      if not isinstance(payload, dict):
        raise RuntimeError(f'{story.story_id} ({story.path}): step[{index}] invalid waitFor')
      sel = payload.get('sel')
      state = str(payload.get('state') or 'visible')
      if state not in {'attached', 'detached', 'visible', 'hidden'}:
        raise RuntimeError(f'{story.story_id} ({story.path}): step[{index}] invalid waitFor.state')
      lines.append(
        f"await page.waitForSelector(resolve({json.dumps(sel)}), {{ state: {json.dumps(state)}, timeout: timeouts.element }});"
      )
      continue

    if kind == 'expectVisible':
      lines.append(
        f"await page.waitForSelector(resolve({json.dumps(payload)}), {{ state: 'visible', timeout: timeouts.element }});"
      )
      continue

    if kind == 'expectUrlContains':
      lines.append("const __url = page.url();")
      lines.append(
        f"if (!__url.includes({json.dumps(payload)})) throw new Error('expectUrlContains failed: ' + {json.dumps(str(payload))} + ' not in ' + __url);"
      )
      continue

    if kind == 'goto':
      # Resolve relative URLs against baseUrl in JS so the agent can pass
      # either "/dashboard" or "http://other-host/path" naturally.
      # Note: don't use `new URL(u, baseUrl)` — Playwright MCP's runCode
      # evaluates this callback in a Node VM context that doesn't expose
      # the URL global, so we splice manually.
      lines.append(
        f"{{ const __u = {json.dumps(payload)};"
        " const __target = /^[a-z]+:\\/\\//i.test(__u)"
        "   ? __u"
        "   : (baseUrl.replace(/\\/+$/, '') + (__u.startsWith('/') ? __u : '/' + __u));"
        " await page.goto(__target, { waitUntil: 'domcontentloaded', timeout: timeouts.navigation }); }"
      )
      continue

    if kind == 'reload':
      lines.append(
        "await page.reload({ waitUntil: 'domcontentloaded', timeout: timeouts.navigation });"
      )
      continue

    if kind == 'back':
      lines.append(
        "await page.goBack({ waitUntil: 'domcontentloaded', timeout: timeouts.navigation });"
      )
      continue

    if kind == 'forward':
      lines.append(
        "await page.goForward({ waitUntil: 'domcontentloaded', timeout: timeouts.navigation });"
      )
      continue

    if kind == 'evaluate':
      # Side-effect JS — no assertion. Use this for setup/seed work that
      # doesn't have a meaningful return value (e.g. POSTing to your API
      # before navigating to the page that displays the data).
      lines.append(f"await page.evaluate({json.dumps(payload)});")
      continue

    if kind == 'waitForUrl':
      # Polls page.url() until it includes the substring or the navigation
      # timeout elapses. Useful after a click that triggers a redirect.
      lines.append(
        f"await page.waitForURL((u) => String(u).includes({json.dumps(payload)}), "
        f"{{ timeout: timeouts.navigation }});"
      )
      continue

    if kind == 'expectCount':
      if not isinstance(payload, dict):
        raise RuntimeError(f'{story.story_id} ({story.path}): step[{index}] invalid expectCount')
      sel = payload.get('sel')
      equals = payload.get('equals')
      lines.append(f"const __count = await page.locator(resolve({json.dumps(sel)})).count();")
      lines.append(
        f"if (__count !== {int(equals)}) throw new Error('expectCount failed: expected ' + {int(equals)} + ', got ' + String(__count));"
      )
      continue

    if kind == 'expectEval':
      if not isinstance(payload, dict):
        raise RuntimeError(f'{story.story_id} ({story.path}): step[{index}] invalid expectEval')
      expr = payload.get('expr')
      expected = payload.get('equals')
      lines.append(f"const __eval = await page.evaluate({json.dumps(expr)});")
      lines.append(
        f"if (__eval !== {json.dumps(expected)}) throw new Error('expectEval failed: expected ' + {json.dumps(str(expected))} + ', got ' + String(__eval));"
      )
      continue

    if kind == 'expectTextEquals':
      if not isinstance(payload, dict):
        raise RuntimeError(f'{story.story_id} ({story.path}): step[{index}] invalid expectTextEquals')
      sel = payload.get('sel')
      expected = payload.get('equals')
      lines.append(f"const __textEq = (await page.locator(resolve({json.dumps(sel)})).innerText()).trim();")
      lines.append(f"const __expectedTextEq = String({json.dumps(expected)}).trim();")
      lines.append(
        "if (__textEq !== __expectedTextEq) throw new Error('expectTextEquals failed: expected ' + __expectedTextEq + ', got ' + __textEq);"
      )
      continue

    if kind == 'expectTextContains':
      if not isinstance(payload, dict):
        raise RuntimeError(f'{story.story_id} ({story.path}): step[{index}] invalid expectTextContains')
      sel = payload.get('sel')
      contains = payload.get('contains')
      lines.append(f"const __text = await page.locator(resolve({json.dumps(sel)})).innerText();")
      lines.append(
        f"if (!__text.includes({json.dumps(contains)})) throw new Error('expectTextContains failed: ' + {json.dumps(str(contains))});"
      )
      continue

    if kind == 'expectAttrEquals':
      if not isinstance(payload, dict):
        raise RuntimeError(f'{story.story_id} ({story.path}): step[{index}] invalid expectAttrEquals')
      sel = payload.get('sel')
      attr = payload.get('attr')
      expected = payload.get('equals')
      lines.append(
        f"const __attrVal = await page.locator(resolve({json.dumps(sel)})).getAttribute({json.dumps(attr)});"
      )
      lines.append(
        f"if (__attrVal !== {json.dumps(expected)}) throw new Error('expectAttrEquals failed: ' + {json.dumps(str(attr))} + ' expected ' + {json.dumps(str(expected))} + ', got ' + String(__attrVal));"
      )
      continue

    if kind in {'expectEnabled', 'expectDisabled'}:
      method = 'isEnabled' if kind == 'expectEnabled' else 'isDisabled'
      lines.append(f"const __ok = await page.locator(resolve({json.dumps(payload)})).{method}();")
      lines.append(
        f"if (!__ok) throw new Error('{kind} failed: ' + resolve({json.dumps(payload)}));"
      )
      continue

    if kind == 'sleepMs':
      if not isinstance(payload, int):
        raise RuntimeError(f'{story.story_id} ({story.path}): step[{index}] invalid sleepMs')
      lines.append(f"await page.waitForTimeout({payload});")
      continue

    raise RuntimeError(f'{story.story_id} ({story.path}): step[{index}] unknown step kind {kind}')

  # Wrap each step's emitted JS in a `{ ... }` block so any `const`
  # declarations stay scoped to the step. Without this, two steps of the
  # same kind (e.g. two `expectTextContains`) both emit `const __text = …`
  # into the same enclosing function scope, hitting a JS SyntaxError
  # ("Identifier '__text' has already been declared") at parse time. That
  # error surfaces upstream as "missing ### Result json" — hard to debug.
  # Each loop iteration above pushes a `_STEP_BOUNDARY` sentinel before
  # emitting; we wrap each between-sentinels chunk here so the per-branch
  # dispatch code stays untouched.
  result: list[str] = []
  buffer: list[str] = []
  for line in lines + [_STEP_BOUNDARY]:
    if line == _STEP_BOUNDARY:
      if buffer:
        result.append('{')
        result.extend(buffer)
        result.append('}')
        buffer = []
    else:
      buffer.append(line)
  return '\n'.join(result)


def _compose_run_code(
  *,
  story: Story,
  run_id: str,
  base_url: str,
  story_dir: Path,
  viewport: Viewport,
  timeouts: Timeouts
) -> str:
  steps_js = _compile_steps(story)
  selectors_json = json.dumps(story.selectors)

  return f"""async page => {{
  const runId = {json.dumps(run_id)};
  const storyId = {json.dumps(story.story_id)};
  const name = {json.dumps(story.name)};
  const criteria = {json.dumps(story.criteria)};
  const baseUrl = {json.dumps(base_url)};
  const viewport = {{ width: {viewport.width}, height: {viewport.height} }};
  const timeouts = {{ navigation: {timeouts.navigation}, element: {timeouts.element} }};
  const storyDir = {json.dumps(str(story_dir) + '/')};

  const selectors = {selectors_json};
  const resolve = (ref) => (typeof ref === 'string' && ref in selectors ? selectors[ref] : ref);

  const checkpoints = [];
  const consoleErrors = [];

  page.on('console', msg => {{
    if (msg.type() === 'error') consoleErrors.push({{ type: 'console', text: msg.text() }});
  }});
  page.on('pageerror', err => {{
    consoleErrors.push({{ type: 'pageerror', text: String(err && err.message ? err.message : err) }});
  }});

  const shot = async (key, label) => {{
    const path = storyDir + key + '.png';
    await page.screenshot({{ path }});
    checkpoints.push({{ key, label, path }});
  }};

  const startedAt = Date.now();
  let status = 'pass';
  let error = null;

  try {{
    await page.setViewportSize(viewport);
    await page.goto(baseUrl, {{ waitUntil: 'domcontentloaded', timeout: timeouts.navigation }});

{_indent(steps_js, 4)}

  }} catch (e) {{
    status = 'fail';
    error = String(e && e.stack ? e.stack : e);
    try {{ await shot('99-error', 'Error state'); }} catch {{}}
  }}

  const endedAt = Date.now();
  return {{
    runId,
    storyId,
    name,
    criteria,
    status,
    durationMs: endedAt - startedAt,
    url: page.url(),
    viewport: page.viewportSize(),
    checkpoints,
    consoleErrors,
    error
  }};
}}"""


def _indent(text: str, spaces: int) -> str:
  prefix = ' ' * spaces
  return '\n'.join(prefix + line if line.strip() else '' for line in text.splitlines())


def _render_run_report(run_dir: Path, run_manifest: dict, video: VideoPolicy) -> Path:
  rows: list[str] = []
  sections: list[str] = []

  for story in run_manifest['stories']:
    story_id = story['storyId']
    status = story.get('status', 'pending')
    status_upper = status.upper()
    duration_ms = story.get('durationMs')
    duration = '' if duration_ms is None else f"{int(duration_ms)}ms"
    error_head = story.get('errorHead') or ''

    rows.append(
      f"<tr class='{status_upper}'><td><a href='#{story_id}'>{story_id}</a></td><td>{_escape(story.get('name',''))}</td><td><span class='pill {status_upper}'>{status_upper}</span></td><td class='muted'>{duration}</td><td class='muted'>{_escape(error_head)}</td></tr>"
    )

    story_dir = run_dir / story_id
    story_manifest_path = story_dir / 'manifest.json'
    story_manifest = None
    if story_manifest_path.exists():
      story_manifest = json.loads(story_manifest_path.read_text(encoding='utf-8'))

    criteria_items = ''.join(f"<li>{_escape(item)}</li>" for item in story.get('criteria', []))

    video_html = "<div class='muted'>No video.</div>"
    video_path = story_dir / 'run.webm'
    if video.record and video_path.exists():
      should_embed = video.embed_policy == 'always' or (video.embed_policy == 'on-fail' and status == 'fail')
      if video.embed_policy == 'never':
        should_embed = False

      if should_embed and video_path.stat().st_size <= video.embed_bytes_limit:
        video_html = f"<video controls preload='none' src='{_embed_webm(video_path)}'></video>"
      elif should_embed:
        video_html = f"<div class='muted'>Video not embedded ({video_path.stat().st_size} bytes).</div>"
      else:
        video_html = "<div class='muted'>Video saved (not embedded).</div>"

    shots_html: list[str] = []
    if story_manifest is not None:
      for cp in story_manifest.get('checkpoints', []):
        img_path = Path(cp.get('path', ''))
        if img_path.exists() and img_path.suffix.lower() == '.png':
          shots_html.append(
            f"<figure><figcaption>{_escape(cp.get('key', ''))} — {_escape(cp.get('label', ''))}</figcaption><img alt='{_escape(cp.get('label', ''))}' src='{_embed_png(img_path)}' /></figure>"
          )

    errors_section = ''
    if story_manifest is not None:
      error_text = story_manifest.get('error') or ''
      console_errors = story_manifest.get('consoleErrors') or []
      console_text = '\n'.join(f"- {e.get('type')}: {e.get('text')}" for e in console_errors)
      errors_section = f"""
        <details>
          <summary>Errors / logs</summary>
          <div class='stack'>
            <div>
              <div class='label'>Automation error</div>
              <pre>{_escape(error_text) if error_text else 'None'}</pre>
            </div>
            <div>
              <div class='label'>Console/page errors</div>
              <pre>{_escape(console_text) if console_text else 'None'}</pre>
            </div>
          </div>
        </details>
      """

    open_attr = ' open' if status == 'fail' else ''

    sections.append(
      f"""
      <section id='{story_id}' class='story'>
        <div class='story-h'>
          <div>
            <h2>{story_id} — {_escape(story.get('name', ''))}</h2>
            <div class='muted'>Status: <span class='pill {status_upper}'>{status_upper}</span> <span class='muted'>{duration}</span></div>
          </div>
          <a class='muted' href='#top'>Back to top</a>
        </div>

        <details{open_attr}>
          <summary>Acceptance criteria</summary>
          <ul>{criteria_items}</ul>
        </details>

        <details{open_attr}>
          <summary>Evidence</summary>
          <div class='stack'>
            <div>
              <div class='label'>Video</div>
              {video_html}
            </div>
            <div>
              <div class='label'>Storyboard</div>
              <div class='grid'>
                {''.join(shots_html) if shots_html else "<div class='muted'>No screenshots yet.</div>"}
              </div>
            </div>
          </div>
        </details>

        {errors_section}
      </section>
      """
    )

  html = f"""<!doctype html>
<html lang='en'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>Run {run_manifest['runId']} evidence</title>
  <style>
    body {{ margin: 16px; font: 13px/1.45 system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; color: #111; background: #fff; }}
    h1 {{ font-size: 16px; margin: 0 0 6px; }}
    h2 {{ font-size: 14px; margin: 0; }}
    a {{ color: inherit; }}
    .muted {{ color: #555; }}
    .pill {{ display:inline-block; padding: 2px 8px; border-radius: 999px; font-weight: 650; border: 1px solid #ddd; font-size: 12px; }}
    .PASS {{ background: #e8fff0; border-color:#bfe7cc; }}
    .FAIL {{ background: #fff0f0; border-color:#f0b9b9; }}
    .PENDING {{ background: #f5f5f5; border-color:#ddd; }}
    .top {{ display: grid; gap: 10px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #eee; vertical-align: top; }}
    th {{ font-weight: 650; }}
    section.story {{ border: 1px solid #ddd; border-radius: 10px; padding: 12px; margin: 14px 0; }}
    .story-h {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }}
    details {{ border: 1px solid #eee; border-radius: 10px; padding: 8px 10px; margin-top: 10px; background: #fafafa; }}
    summary {{ cursor: pointer; font-weight: 650; }}
    ul {{ margin: 8px 0 0 18px; }}
    .grid {{ display: grid; gap: 12px; }}
    figure {{ margin: 0; }}
    figcaption {{ font-weight: 650; margin: 0 0 6px; }}
    img {{ width: 100%; height: auto; border: 1px solid #eee; border-radius: 8px; background: #fff; }}
    video {{ width: 100%; border: 1px solid #eee; border-radius: 8px; background: #000; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #fff; border: 1px solid #eee; padding: 10px; border-radius: 8px; margin: 0; }}
    .stack {{ display: grid; gap: 12px; margin-top: 10px; }}
    .label {{ font-weight: 650; margin-bottom: 6px; }}
  </style>
</head>
<body>
  <div id='top' class='top'>
    <div>
      <h1>Acceptance test run</h1>
      <div class='muted'>Run: <code>{_escape(run_manifest['runId'])}</code> · Base URL: <code>{_escape(run_manifest['baseUrl'])}</code></div>
    </div>

    <div>
      <table>
        <thead>
          <tr><th>Story</th><th>Name</th><th>Status</th><th>Duration</th><th>Notes</th></tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </div>
  </div>

  {''.join(sections)}
</body>
</html>
"""

  out_path = run_dir / 'run.report.embedded.html'
  out_path.write_text(html, encoding='utf-8')
  return out_path


def _build_env(browser: BrowserConfig) -> dict[str, str]:
  env = dict(os.environ)
  if browser.display:
    env['DISPLAY'] = browser.display
  return env


def _cli_prefix(browser: BrowserConfig) -> list[str]:
  prefix = ['playwright-cli']
  if browser.session:
    prefix.append(f"-s={browser.session}")
  return prefix


def _open_args(base_url: str, browser: BrowserConfig) -> list[str]:
  args: list[str] = ['open']
  if browser.headed:
    args.append('--headed')
  if browser.profile:
    args.append(f"--profile={browser.profile}")
  elif browser.persistent:
    args.append('--persistent')
  args.append(base_url)
  return args


def _maybe_pause_after_open(browser: BrowserConfig) -> None:
  if not browser.pause_after_open:
    return
  if not sys.stdin.isatty():
    print('pauseAfterOpen requested but stdin is not a TTY; skipping', file=sys.stderr)
    return
  input('Browser opened. Complete any manual login, then press Enter to continue... ')


def _maybe_state_load(prefix: list[str], auth: AuthConfig, env: dict[str, str]) -> None:
  if not auth.state_load_path:
    return
  result = _run(prefix + ['state-load', auth.state_load_path], env=env)
  if result.returncode != 0:
    raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'state-load failed')


def _maybe_state_save(prefix: list[str], auth: AuthConfig, env: dict[str, str]) -> None:
  if not auth.state_save_path:
    return
  Path(auth.state_save_path).parent.mkdir(parents=True, exist_ok=True)
  result = _run(prefix + ['state-save', auth.state_save_path], env=env)
  if result.returncode != 0:
    raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'state-save failed')


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument('--base-url', default=None)
  parser.add_argument('--out-root', default=None)
  parser.add_argument('--run-id', default=None)
  parser.add_argument('--stories', nargs='*', default=None)
  parser.add_argument('--list-stories', action='store_true', default=False)
  parser.add_argument('--session', default=None)
  parser.add_argument('--headed', action='store_true', default=False)
  parser.add_argument('--display', default=None)
  parser.add_argument('--reuse-session', action='store_true', default=False)
  parser.add_argument('--persistent', action='store_true', default=False)
  parser.add_argument('--profile', default=None)
  parser.add_argument('--pause-after-open', action='store_true', default=False)
  parser.add_argument('--state-load', default=None)
  parser.add_argument('--state-save', default=None)
  parser.add_argument('--config', default=str(Path(__file__).with_name('config.json')))
  parser.add_argument('--stories-dir', default=str(Path(__file__).with_name('stories')))
  parser.add_argument(
    '--wait-timeout-s',
    type=int,
    default=30,
    help='How long to poll --base-url before giving up. The runner does NOT '
         'start the app — bring it up yourself first (Procfile, docker, etc.).',
  )

  args = parser.parse_args()

  config_path = Path(args.config)
  stories_dir = Path(args.stories_dir)

  base_url, viewport, timeouts, video, browser, auth = _load_config(config_path)
  if args.base_url:
    base_url = args.base_url

  if args.session is not None:
    browser = BrowserConfig(
      session=str(args.session).strip() or None,
      headed=browser.headed,
      display=browser.display,
      reuse_session=browser.reuse_session,
      persistent=browser.persistent,
      profile=browser.profile,
      pause_after_open=browser.pause_after_open
    )

  if args.headed:
    browser = BrowserConfig(
      session=browser.session,
      headed=True,
      display=browser.display,
      reuse_session=browser.reuse_session,
      persistent=browser.persistent,
      profile=browser.profile,
      pause_after_open=browser.pause_after_open
    )

  if args.display is not None:
    browser = BrowserConfig(
      session=browser.session,
      headed=browser.headed,
      display=str(args.display).strip() or None,
      reuse_session=browser.reuse_session,
      persistent=browser.persistent,
      profile=browser.profile,
      pause_after_open=browser.pause_after_open
    )

  if args.reuse_session:
    browser = BrowserConfig(
      session=browser.session,
      headed=browser.headed,
      display=browser.display,
      reuse_session=True,
      persistent=browser.persistent,
      profile=browser.profile,
      pause_after_open=browser.pause_after_open
    )

  if args.persistent:
    browser = BrowserConfig(
      session=browser.session,
      headed=browser.headed,
      display=browser.display,
      reuse_session=browser.reuse_session,
      persistent=True,
      profile=browser.profile,
      pause_after_open=browser.pause_after_open
    )

  if args.profile is not None:
    profile_path = _resolve_path(config_path.parent, str(args.profile).strip() or None)
    browser = BrowserConfig(
      session=browser.session,
      headed=browser.headed,
      display=browser.display,
      reuse_session=browser.reuse_session,
      persistent=browser.persistent,
      profile=profile_path,
      pause_after_open=browser.pause_after_open
    )

  if args.pause_after_open:
    browser = BrowserConfig(
      session=browser.session,
      headed=browser.headed,
      display=browser.display,
      reuse_session=browser.reuse_session,
      persistent=browser.persistent,
      profile=browser.profile,
      pause_after_open=True
    )

  if args.state_load is not None:
    auth = AuthConfig(
      state_load_path=_resolve_path(config_path.parent, str(args.state_load).strip() or None),
      state_save_path=auth.state_save_path
    )

  if args.state_save is not None:
    auth = AuthConfig(
      state_load_path=auth.state_load_path,
      state_save_path=_resolve_path(config_path.parent, str(args.state_save).strip() or None)
    )

  stories = _load_stories(stories_dir)
  stories_by_id = {s.story_id: s for s in stories}

  if args.list_stories:
    for story in stories:
      print(f"{story.story_id} - {story.name}")
    return 0

  run_id = args.run_id or _now_run_id()
  out_root = Path(args.out_root) if args.out_root else Path.cwd() / 'artifacts'
  run_dir = out_root / run_id
  run_dir.mkdir(parents=True, exist_ok=True)

  selected = args.stories if args.stories and len(args.stories) > 0 else [s.story_id for s in stories]

  env = _build_env(browser)
  prefix = _cli_prefix(browser)

  # The runner does NOT start the app under test. Bring it up yourself
  # before invoking the runner (e.g. via Procfile, docker-compose, or
  # whatever idiom the project uses; see AGENTS.md). We poll --base-url
  # until it responds 2xx or --wait-timeout-s expires.
  _wait_for_url(base_url, args.wait_timeout_s)

  run_manifest_path = run_dir / 'run.manifest.json'
  run_manifest = {
    'runId': run_id,
    'baseUrl': base_url,
    'stories': [
      {
        'storyId': s.story_id,
        'name': s.name,
        'criteria': s.criteria,
        'status': 'pending'
      }
      for s in stories
    ]
  }

  run_manifest_path.write_text(json.dumps(run_manifest, indent=2), encoding='utf-8')
  _render_run_report(run_dir, run_manifest, video)

  any_failed = False

  try:
    if browser.reuse_session:
      _run(prefix + ['close'], env=env)
      open_result = _run(prefix + _open_args(base_url, browser), env=env)
      if open_result.returncode != 0:
        raise RuntimeError(open_result.stderr.strip() or open_result.stdout.strip() or 'playwright-cli open failed')

      _maybe_state_load(prefix, auth, env)
      _maybe_pause_after_open(browser)

    for story_id in selected:
      if story_id not in stories_by_id:
        raise RuntimeError(f'unknown story id: {story_id}')

      story = stories_by_id[story_id]
      story_dir = run_dir / story.story_id
      story_dir.mkdir(parents=True, exist_ok=True)

      if not browser.reuse_session:
        _run(prefix + ['close'], env=env)
        open_result = _run(prefix + _open_args(base_url, browser), env=env)
        if open_result.returncode != 0:
          raise RuntimeError(open_result.stderr.strip() or open_result.stdout.strip() or 'playwright-cli open failed')

        _maybe_state_load(prefix, auth, env)
        _maybe_pause_after_open(browser)

      if video.record:
        _run(prefix + ['video-start'], env=env)

      code = _compose_run_code(
        story=story,
        run_id=run_id,
        base_url=base_url,
        story_dir=story_dir,
        viewport=viewport,
        timeouts=timeouts
      )

      run_result = _run(prefix + ['run-code', code], env=env)

      if video.record:
        _run(prefix + ['video-stop', f"--filename={story_dir / 'run.webm'}"], env=env)

      if run_result.returncode != 0:
        manifest = {
          'runId': run_id,
          'storyId': story.story_id,
          'name': story.name,
          'criteria': story.criteria,
          'status': 'fail',
          'durationMs': None,
          'url': base_url,
          'viewport': {'width': viewport.width, 'height': viewport.height},
          'checkpoints': [],
          'consoleErrors': [],
          'error': run_result.stderr.strip() or run_result.stdout.strip() or 'run-code failed'
        }
      else:
        manifest = _parse_playwright_result(run_result.stdout)

      (story_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

      error_value = manifest.get('error')
      error_head = ''
      if isinstance(error_value, str) and error_value:
        error_head = error_value.splitlines()[0][:140]

      for rec in run_manifest['stories']:
        if rec['storyId'] == story.story_id:
          rec['status'] = manifest.get('status', 'fail')
          rec['durationMs'] = manifest.get('durationMs')
          rec['errorHead'] = error_head
          break

      run_manifest_path.write_text(json.dumps(run_manifest, indent=2), encoding='utf-8')
      _render_run_report(run_dir, run_manifest, video)

      if manifest.get('status') == 'pass':
        _maybe_state_save(prefix, auth, env)

      if manifest.get('status') != 'pass':
        any_failed = True

  finally:
    _run(prefix + ['close'], env=env)

  print(str(run_dir / 'run.report.embedded.html'))
  return 1 if any_failed else 0


if __name__ == '__main__':
  try:
    raise SystemExit(main())
  except KeyboardInterrupt:
    raise
  except Exception as exc:
    print(str(exc), file=sys.stderr)
    raise
