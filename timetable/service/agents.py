"""Agents of the 24/7 service. Same base class, message protocol, logging and tool limits as the
pipeline agents; their state lives in :class:`ServiceStore` instead of a run blackboard.

* Intake    - triages change requests from university staff;
* Watcher   - watches the data file and the requirements, proposes the next job;
* Publisher - release gate: publishes only a checked, needed or better timetable + change notice.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import DATASET_DIRS
from ..core.agent import BaseAgent, RunContext
from ..domain.preview import preview_texts
from .store import ServiceStore
from .versions import diff_rows, quality_score, read_json, read_rows, snapshot

DAY_RU = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт", "Fri": "Пт", "Sat": "Сб"}
IMPROVE_MARGIN = 0.02  # an improvement must cut the penalty score by at least 2 %
FAILURE_PAUSE_S = 1800  # three failed jobs in a row -> pause automatic jobs for 30 minutes


@dataclass
class ServiceContext(RunContext):
    store: ServiceStore | None = None  # type: ignore[assignment]


def find_dataset(name: str) -> Path:
    for d in DATASET_DIRS:
        p = d / name
        if Path(name).name == name and p.is_file():
            return p
    raise FileNotFoundError(f"dataset '{name}' not found in data/, dataset-archive/ or data/uploads/")


def describe_spec(spec: dict) -> str:
    """Constraint spec (JSON DSL) -> short Russian sentence."""
    sc = spec.get("scope") or {}
    who = ", ".join(filter(None, [
        f"программы {', '.join(sc['programs'])}" if sc.get("programs") else "",
        f"{', '.join(map(str, sc['years']))} курс" if sc.get("years") else "",
        f"группы {', '.join(sc['groups'])}" if sc.get("groups") else "",
        f"дисциплина «{', '.join(sc['courses'])}»" if sc.get("courses") else ""])) or "все группы"
    days = ", ".join(DAY_RU.get(d, d) for d in spec.get("days") or []) or "все дни"
    hours = "весь день" if (spec.get("start_hour"), spec.get("end_hour")) == (8, 20) else \
        f"{spec.get('start_hour')}:00–{spec.get('end_hour')}:00"
    if spec["kind"] == "max_daily_hours":
        return f"не более {spec.get('value')} ак. часов в день — {who}"
    if spec["kind"] == "room_unavailable":
        return f"аудитория {', '.join(sc.get('rooms') or [])} закрыта: {days}, {hours}"
    return f"нет занятий: {who}; {days}, {hours}"


def live_term(profile: dict, settings_term_start: str, now: float) -> bool:
    """True once the term has started: students rely on the timetable, so changes must stay small."""
    start = profile.get("stable_after") or settings_term_start
    return time.strftime("%Y-%m-%d", time.localtime(now)) >= start


def config_hash(profile: dict, requirements: list[str]) -> str:
    """Fingerprint of everything that defines the wanted timetable except the data file."""
    keys = ("dataset", "term", "programs")
    blob = json.dumps([{k: profile[k] for k in keys}, requirements], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


class ServiceAgent(BaseAgent):
    @property
    def store(self) -> ServiceStore:
        return self.ctx.store  # type: ignore[attr-defined]


# ------------------------------------------------------------------------------------------
class IntakeAgent(ServiceAgent):
    name = "intake"
    role = ("Triages change requests from university staff: checks that a request is understood and names "
            "real groups, rooms and programmes, then accepts it as a standing rule, rejects it with a reason "
            "or hands it to a human dispatcher.")
    done_when = "every new request has a verdict: accepted, rejected (with the reason) or waiting for a human"

    def register_tools(self) -> None:
        self.add_tool("ground", lambda path, text: preview_texts(path, [text]),
                      "offline parse of the request + check of groups/rooms/programmes against the data")
        self.add_tool("summarize", self._summarize, "one-line Russian summary for the dispatcher (LLM, template fallback)")
        self.add_tool("record", lambda rid, **fields: self.store.update_request(rid, **fields),
                      "store the verdict on the request", retryable=False)

    def _summarize(self, text: str, verdict: dict) -> str:
        return self.ctx.llm.text(
            self.name, "summarize_request",
            system="You help the timetable dispatcher of a university. In one short Russian sentence say what the "
                   "staff request asks for and whom it affects. No preamble.",
            user=json.dumps({"request": text, "verdict": verdict}, ensure_ascii=False),
            offline=lambda: verdict.get("understood") or f"Заявка: {text}")

    def on_triage(self, request_id: int) -> dict:
        req = self.store.request(request_id)
        if req is None:
            raise KeyError(f"request {request_id} not found")
        g = self.call_tool("ground", path=find_dataset(self.store.profile()["dataset"]), text=req["text"])
        if g["parsed"] and any(p["unknown"] for p in g["parsed"]):
            unknown = sorted({u for p in g["parsed"] for u in p["unknown"]})
            status, verdict = "rejected", {"reason": f"в данных вуза нет: {', '.join(unknown)}"}
        elif g["parsed"]:
            spec = g["parsed"][0]
            understood = describe_spec(spec)
            status, verdict = "accepted", {"understood": understood[:1].upper() + understood[1:], "kind": spec["kind"],
                                           "hard": spec["hard"], "reason": "правило понято, объекты найдены в данных"}
        else:
            status, verdict = "review", {"reason": "типовые правила не распознали фразу - решение за диспетчером "
                                                   "(с LLM агент «Ограничения» разберёт её при генерации)"}
        verdict["summary"] = self.call_tool("summarize", text=req["text"], verdict=verdict)
        self.call_tool("record", rid=request_id, status=status, verdict=verdict)
        return {"request": request_id, "status": status, "reason": verdict["reason"]}


# ------------------------------------------------------------------------------------------
class WatcherAgent(ServiceAgent):
    name = "watcher"
    role = ("Watches the university data file and the request inbox around the clock and proposes the next "
            "job: first generation, re-generation after changes, improvement when idle, or wait.")
    done_when = "the current state is scanned and exactly one proposal (a job or wait) is made"

    def register_tools(self) -> None:
        self.add_tool("fingerprint", lambda path: hashlib.sha1(path.read_bytes()).hexdigest()[:12],
                      "content hash of the data workbook (detects edits by the registrar)")
        self.add_tool("inbox", lambda: [r["id"] for r in reversed(self.store.requests(("new",)))],
                      "ids of requests nobody has triaged yet")
        self.add_tool("propose", self._propose, "state -> next job (kind, reason, run parameters)", retryable=False)

    def on_scan(self) -> dict:
        profile = self.store.profile()
        fp = self.call_tool("fingerprint", path=find_dataset(profile["dataset"]))
        return {"fingerprint": fp, "new_requests": self.call_tool("inbox")}

    def on_propose(self, fingerprint: str) -> dict:
        return self.call_tool("propose", fingerprint=fingerprint, now=time.time())

    def _propose(self, fingerprint: str, now: float) -> dict:
        profile, store = self.store.profile(), self.store
        reqs = store.standing_requirements()
        cfg = config_hash(profile, reqs)
        cur = store.current_version()
        meta = (cur or {}).get("metrics") or {}
        jobs = store.jobs(10)
        failed = 0
        for j in jobs:
            if j["status"] != "failed":
                break
            failed += 1
        wait = lambda reason: {"kind": "wait", "reason": reason}  # noqa: E731
        if failed >= 3 and now - (jobs[0]["finished"] or jobs[0]["ts"]) < FAILURE_PAUSE_S:
            return wait("три задания подряд завершились ошибкой - пауза 30 минут, нужна проверка диспетчера")
        if store.get("paused_until", 0) > now:
            return wait("автоматические задания приостановлены диспетчером")

        kind, reason, variant = None, "", 0
        if cur is None:
            kind, reason = "generate", "опубликованной версии ещё нет"
        elif meta.get("fingerprint") != fingerprint:
            kind, reason = "regenerate", "в Excel вуза изменились данные"
        elif meta.get("config") != cfg:
            new = store.requests(("accepted",))
            kind, reason = "regenerate", (f"приняты новые заявки: {len(new)}" if new else "изменились требования профиля")
        else:
            since = [j for j in jobs if j["ts"] > cur["ts"] and j["kind"] == "improve"]
            last_end = max([j["finished"] or j["ts"] for j in jobs] or [cur["ts"]])
            if len(since) >= profile["max_improve_attempts"]:
                return wait(f"версия v{cur['id']} актуальна; улучшений не найдено за {len(since)} попыток")
            due = last_end + profile["improve_every_min"] * 60
            if now < due:
                return wait(f"версия v{cur['id']} актуальна; следующий поиск улучшений через "
                            f"{max(1, round((due - now) / 60))} мин")
            kind, variant = "improve", len(since) + 1
            reason = f"поиск улучшения v{cur['id']}: вариант {variant} из {profile['max_improve_attempts']}"

        live = live_term(profile, self.ctx.settings.term_start, now)
        warm = None  # draft period: improvements search freely; live term: every job starts from the published version
        if cur and profile["warm_start"] and (kind == "regenerate" or live):
            starts = store.version_dir(cur["id"]) / "starts.json"
            warm = str(starts) if starts.exists() else None
        seconds = float(profile["solver_seconds"]) * (1 + 0.5 * variant)  # improvements get more solver time
        params = {"dataset": profile["dataset"], "term": profile["term"], "programs": profile["programs"],
                  "requirements": reqs, "solver_seconds": min(seconds, 120), "llm": profile["llm"],
                  "seed": 7 + 101 * variant, "warm_start_path": warm, "fingerprint": fingerprint, "config": cfg,
                  "live": live}
        return {"kind": kind, "reason": reason, "params": params}


# ------------------------------------------------------------------------------------------
class PublisherAgent(ServiceAgent):
    name = "publisher"
    role = ("Release gate of the published timetable: checks a finished run, compares it with the current "
            "version, publishes it only when it has no hard violations and is needed or better, and writes the "
            "change notice for students and staff.")
    done_when = "the run is published as a new version or kept back with a stated reason"

    def register_tools(self) -> None:
        self.add_tool("evaluate", self._evaluate, "run result vs current version: score, diff, verdict", retryable=False)
        self.add_tool("publish", self._publish, "snapshot the run as a new immutable version", retryable=False)
        self.add_tool("announce", self._announce, "change notice for students and staff (LLM, template fallback)")

    def _evaluate(self, run_dir: Path, kind: str, live: bool = False) -> dict:
        res = read_json(run_dir / "result.json")
        hard = sum((res.get("hard_violations") or {}).values()) if res.get("hard_violations") is not None else None
        score = quality_score(res.get("soft") or {})
        cur = self.store.current_version()
        out: dict[str, Any] = {"status": res.get("status"), "hard": hard, "score": score,
                               "current_score": cur["score"] if cur else None, "soft": res.get("soft") or {}}
        if res.get("status") != "ok" or hard:
            return {**out, "decision": "reject",
                    "reason": f"прогон завершился со статусом {res.get('status')}: {res.get('reason') or f'{hard} жёстких нарушений'}"}
        old = read_rows(self.store.version_dir(cur["id"]) / "timetable.csv") if cur else []
        out["diff"] = diff_rows(old, read_rows(run_dir / "timetable.csv"))
        if kind == "improve" and cur and score > cur["score"] * (1 - IMPROVE_MARGIN):
            return {**out, "decision": "keep",
                    "reason": f"штраф {score} не лучше опубликованного {cur['score']} (нужно -{IMPROVE_MARGIN:.0%})"}
        limit = self.store.profile()["max_moved_share"] * out["diff"]["total_hours"]
        if kind == "improve" and live and out["diff"]["hours_moved"] > limit:
            return {**out, "decision": "keep",
                    "reason": f"идёт семестр: улучшение двигает {out['diff']['hours_moved']} ч, допустимо не более {limit:.0f} ч"}
        why = {"generate": "первая версия", "regenerate": "учтены изменения",
               "improve": f"штраф {cur['score'] if cur else '-'} -> {score}"}.get(kind, kind)
        return {**out, "decision": "publish", "reason": f"0 жёстких нарушений, {why}"}

    def _publish(self, run_id: str, run_dir: Path, job_id: int, ev: dict, params: dict) -> int:
        metrics = {**ev["soft"], "fingerprint": params.get("fingerprint"), "config": params.get("config"),
                   "requirements": params.get("requirements", []), "term": params.get("term"),
                   "programs": params.get("programs", []), "kind": self.store.job(job_id)["kind"]}
        vid = self.store.add_version(run_id, job_id, ev["score"], metrics, ev["diff"], "")
        snapshot(run_dir, self.store.version_dir(vid))
        for r in self.store.requests(("accepted",)):  # these rules are now part of the published timetable
            if r["text"] in metrics["requirements"]:
                self.store.update_request(r["id"], status="applied", job_id=job_id)
        return vid

    def _announce(self, vid: int, reason: str, ev: dict) -> str:
        d = ev["diff"]
        prof = self.store.profile()

        def offline() -> str:
            if d.get("first"):
                change = f"Это первая версия: {d['total_hours']} академ. часов в неделю."
            else:
                change = (f"Перенесено по времени: {d['hours_moved']} ч, смена аудитории: {d['room_changes']} ч, "
                          f"затронуто групп: {d['groups_affected']}.")
            return (f"Опубликована версия v{vid} расписания {prof['university']} (триместр {prof['term']}). "
                    f"Причина: {reason}. {change} Жёстких нарушений нет, окна: "
                    f"{ev['soft'].get('gap_hours_per_group')} ч на группу в неделю.")
        text = self.ctx.llm.text(
            self.name, "announce_version",
            system="You write a short notice (Russian, 2-4 sentences, no markdown) for students and staff of a "
                   "university about a new published timetable version: why it was published and what changed.",
            user=json.dumps({"version": vid, "university": prof["university"], "reason": reason,
                             "diff": {k: v for k, v in d.items() if k != "groups"}, "quality": ev["soft"]},
                            ensure_ascii=False),
            offline=offline)
        self.store.set_announcement(vid, text)
        return text

    def on_review(self, job_id: int, run_dir: str) -> dict:
        job = self.store.job(job_id)
        ev = self.call_tool("evaluate", run_dir=Path(run_dir), kind=job["kind"], live=bool(job["params"].get("live")))
        outcome = {k: ev.get(k) for k in ("decision", "reason", "score", "current_score", "hard")}
        if ev.get("diff"):
            outcome["diff"] = {k: v for k, v in ev["diff"].items() if k != "groups"}
        if ev["decision"] == "publish":
            vid = self.call_tool("publish", run_id=job["run_id"], run_dir=Path(run_dir), job_id=job_id, ev=ev,
                                 params=job["params"])
            outcome["version"] = vid
            outcome["announcement"] = self.call_tool("announce", vid=vid, reason=ev["reason"], ev=ev)
        status = {"publish": "published", "keep": "kept"}.get(ev["decision"], "failed")
        self.store.update_job(job_id, status=status, outcome=outcome, finished=time.time())
        return {"job": job_id, "status": status, **outcome}


SERVICE_AGENT_CLASSES = [IntakeAgent, WatcherAgent, PublisherAgent]
