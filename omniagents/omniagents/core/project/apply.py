from __future__ import annotations

import json
from pathlib import Path
import yaml

from typing import Any

from omniagents.core.project.runtime import load_project


def _nonempty_str(value: Any) -> bool:
    """True when ``value`` is a non-blank string."""
    return isinstance(value, str) and bool(value.strip())


def apply_judge_updates(
    proj_path: str,
    judge_key: str,
    new_instructions: str | None,
    new_template: str | None,
) -> None:
    from pathlib import Path as _P

    def _backup(fp: _P):
        return None

    def _find_judge_source(proj_path: _P, key: str) -> tuple[_P, dict, str | None]:
        pj = load_project(str(proj_path))
        paths = pj.paths.get("evaluations") or []
        cands: list[_P] = []
        for ed in paths:
            p = _P(ed)
            if p.is_dir():
                try:
                    for y in (
                        (p / "judges").rglob("*.yml") if (p / "judges").exists() else []
                    ):
                        cands.append(y)
                except Exception:
                    pass
        seen = set()
        uniq: list[_P] = []
        for c in cands:
            s = str(c.resolve())
            if s in seen:
                continue
            seen.add(s)
            if c.exists():
                uniq.append(c)
        for y in uniq:
            try:
                doc = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            js = doc.get("judges")
            if isinstance(js, dict) and key in js:
                return y, js.get(key) or {}, None
            if isinstance(js, list):
                for it in js:
                    nm = (it or {}).get("key") or (it or {}).get("name")
                    if nm == key:
                        return y, it or {}, None
        return _P(""), {}, None

    def _resolve_ref(base_file: _P, jcfg: dict) -> tuple[_P, dict | None]:
        ref = jcfg.get("agent_ref") or jcfg.get("ref")
        if isinstance(ref, str) and ref.strip():
            rp = _P(ref)
            if not rp.is_absolute():
                rp = (base_file.parent / rp).resolve()
            try:
                sub = yaml.safe_load(rp.read_text(encoding="utf-8")) or {}
            except Exception:
                sub = {}
            return rp, sub
        return base_file, None

    def _apply_to_yaml(
        doc: dict, jkey: str, new_instr: str | None, new_tmpl: str | None
    ) -> tuple[dict, bool]:
        changed = False
        jblock = doc.get("judges")
        targets: list[dict] = []
        if isinstance(jblock, dict) and jkey in jblock:
            targets = [jblock[jkey]]
        elif isinstance(jblock, list):
            for it in jblock:
                nm = (it or {}).get("key") or (it or {}).get("name")
                if nm == jkey:
                    targets = [it]
                    break
        for tgt in targets:
            ag = tgt.get("agent") or {}
            inp = tgt.get("input") or {}
            if new_instr is not None:
                if _nonempty_str(ag.get("instructions_file")):
                    pass
                else:
                    ag["instructions"] = new_instr
                    tgt["agent"] = ag
                    changed = True
            if new_tmpl is not None:
                if _nonempty_str(inp.get("template_file")):
                    pass
                else:
                    inp["template"] = new_tmpl
                    tgt["input"] = inp
                    changed = True
        return doc, changed

    def _write_yaml(fp: _P, jkey: str, data: dict):
        class _LS(str):
            pass

        class _D(yaml.SafeDumper):
            pass

        def _rep(dumper, value):
            return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")

        _D.add_representer(_LS, _rep)

        def _lit(v):
            if isinstance(v, str) and "\n" in v:
                return _LS(v)
            return v

        blob = json.loads(json.dumps(data))
        try:
            j = blob.get("judges")
            if isinstance(j, dict):
                t = j.get(judge_key) or {}
                if isinstance(t, dict):
                    if (
                        "agent" in t
                        and isinstance(t["agent"], dict)
                        and "instructions" in t["agent"]
                    ):
                        t["agent"]["instructions"] = _lit(t["agent"]["instructions"])
                    if (
                        "input" in t
                        and isinstance(t["input"], dict)
                        and "template" in t["input"]
                    ):
                        t["input"]["template"] = _lit(t["input"]["template"])
            elif isinstance(j, list):
                for it in j:
                    nm = (it or {}).get("key") or (it or {}).get("name")
                    if nm == judge_key:
                        if (
                            "agent" in it
                            and isinstance(it["agent"], dict)
                            and "instructions" in it["agent"]
                        ):
                            it["agent"]["instructions"] = _lit(
                                it["agent"]["instructions"]
                            )
                        if (
                            "input" in it
                            and isinstance(it["input"], dict)
                            and "template" in it["input"]
                        ):
                            it["input"]["template"] = _lit(it["input"]["template"])
        except Exception:
            pass
        fp.write_text(
            yaml.dump(blob, Dumper=_D, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    base = Path(proj_path).resolve()
    if base.is_file():
        base = base.parent
    src_file, jcfg_src, _ = _find_judge_source(base, judge_key)
    if not str(src_file):
        return
    ref_file, subdoc = _resolve_ref(src_file, jcfg_src)
    if subdoc is not None and isinstance(subdoc, dict):
        ag = subdoc.get("agent") or {}
        inp = subdoc.get("input") or {}
        if new_instructions is not None and _nonempty_str(ag.get("instructions_file")):
            _rel = str(ag.get("instructions_file"))
            fp = Path(_rel)
            if not fp.is_absolute():
                cand = (ref_file.parent / fp).resolve()
                fp = cand
            if fp.exists():
                _backup(fp)
            fp.write_text(new_instructions, encoding="utf-8")
            print(f"Applied judge instructions: {fp}")
        elif new_instructions is not None:
            if ref_file.exists():
                _backup(ref_file)
            ag["instructions"] = new_instructions
            subdoc["agent"] = ag
            _write_yaml(ref_file, judge_key, subdoc)
            print(f"Applied judge instructions inline: {ref_file}")
        if new_template is not None and _nonempty_str(inp.get("template_file")):
            _rel = str(inp.get("template_file"))
            fp = Path(_rel)
            if not fp.is_absolute():
                cand = (ref_file.parent / fp).resolve()
                fp = cand
            if fp.exists():
                _backup(fp)
            fp.write_text(new_template, encoding="utf-8")
            print(f"Applied judge template: {fp}")
        elif new_template is not None:
            if ref_file.exists():
                _backup(ref_file)
            inp["template"] = new_template
            subdoc["input"] = inp
            _write_yaml(ref_file, judge_key, subdoc)
            print(f"Applied judge template inline: {ref_file}")
    else:
        try:
            doc = yaml.safe_load(src_file.read_text(encoding="utf-8")) or {}
        except Exception:
            doc = {}
        to_write = False
        if new_instructions is not None or new_template is not None:
            jblock = doc.get("judges")
            tgt = None
            if isinstance(jblock, dict) and judge_key in jblock:
                tgt = jblock[judge_key]
            elif isinstance(jblock, list):
                for it in jblock:
                    nm = (it or {}).get("key") or (it or {}).get("name")
                    if nm == judge_key:
                        tgt = it
                        break
            if isinstance(tgt, dict):
                ag = tgt.get("agent") or {}
                inp = tgt.get("input") or {}
                if new_instructions is not None:
                    if _nonempty_str(ag.get("instructions_file")):
                        _rel = str(ag.get("instructions_file"))
                        fp = Path(_rel)
                        if not fp.is_absolute():
                            cand = (src_file.parent / fp).resolve()
                            fp = cand
                        if fp.exists():
                            _backup(fp)
                        fp.write_text(new_instructions, encoding="utf-8")
                        print(f"Applied judge instructions: {fp}")
                    else:
                        tgt.setdefault("agent", {})
                        tgt["agent"]["instructions"] = new_instructions
                        to_write = True
                if new_template is not None:
                    if _nonempty_str(inp.get("template_file")):
                        _rel = str(inp.get("template_file"))
                        fp = Path(_rel)
                        if not fp.is_absolute():
                            cand = (src_file.parent / fp).resolve()
                            fp = cand
                        if fp.exists():
                            _backup(fp)
                        fp.write_text(new_template, encoding="utf-8")
                        print(f"Applied judge template: {fp}")
                    else:
                        tgt.setdefault("input", {})
                        tgt["input"]["template"] = new_template
                        to_write = True
            else:
                mod, changed = _apply_to_yaml(
                    doc, judge_key, new_instructions, new_template
                )
                if changed:
                    to_write = True
                    doc = mod
        if to_write:
            _backup(src_file)
            _write_yaml(src_file, judge_key, doc)
            print(f"Applied judge changes: {src_file}")
