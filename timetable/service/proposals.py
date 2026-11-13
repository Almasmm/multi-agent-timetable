"""Operator proposals discussed by the agents.

The operator writes a proposal in plain words ("move Calculus of IT-2301 off Monday morning", "first year ends by
17:00"), optionally attached to concrete classes picked in the timetable. The agents discuss it in a thread:

1. **Приёмная** (Intake) decides what it is: a change of concrete classes or a general rule.
2. A change goes to the **Планировщик** (Scheduler): with its tools it looks for options and puts concrete moves into
   the draft (nothing is published). A rule goes to **Ограничения** (Constraints): it explains how it understood the
   rule and suggests hard or soft - without adding it.
3. **Критик** (Validator) checks the draft with the independent checker.
4. The operator reads the discussion, may answer (the discussion continues) and accepts or rejects. Accepting a change
   publishes the draft (Publisher); accepting a rule adds it and asks the Watcher for a warm-start regeneration.

Every agent turn uses the same tools as the agent chat (``service/chat.py``), with Claude when available and the
offline rules otherwise; discussions run one at a time in a background thread (the draft is shared).
"""
from __future__ import annotations

import re
import threading
import traceback

from .chat import DAY_RU, AgentChat, summarize

MOVE_WORDS = re.compile(r"перенес|перенос|передвин|сдвин|поменя|замени|поставь|убери|освобод|move|swap|shift|аудитори[юя] для|в другую аудиторию", re.I)
OPEN = ("discussing",)


class ProposalDesk:
    def __init__(self, store, chat: AgentChat):
        self.store, self.chat, self.tools = store, chat, chat.tools
        self._lock = threading.Lock()  # one discussion at a time: the draft is shared
        self.threads: dict[int, threading.Thread] = {}

    # --- API ---------------------------------------------------------------------------------------
    def list(self) -> list[dict]:
        return self.store.proposals()

    def get(self, pid: int) -> dict:
        p = self.store.proposal(pid)
        if p is None:
            raise FileNotFoundError(f"предложения №{pid} нет")
        return {**p, "messages": self.store.proposal_msgs(pid)}

    def create(self, text: str, context: list[dict] | None = None, wait: bool = False) -> dict:
        text = text.strip()
        if len(text) < 3:
            raise ValueError("опишите предложение")
        pid = self.store.add_proposal(text, context or [])
        self.store.add_proposal_msg(pid, "operator", "user", text)
        self._start(pid, text, wait)
        return self.get(pid)

    def reply(self, pid: int, text: str, wait: bool = False) -> dict:
        p = self.get(pid)
        if p["status"] in ("discussing",):
            raise RuntimeError("агенты ещё обсуждают — дождитесь ответа")
        if p["status"] in ("accepted", "rejected"):
            raise RuntimeError("предложение уже закрыто")
        self.store.add_proposal_msg(pid, "operator", "user", text.strip())
        self._start(pid, text.strip(), wait)
        return self.get(pid)

    def accept(self, pid: int, mode: str | None = None) -> dict:
        p = self.get(pid)
        if p["status"] != "ready":
            raise RuntimeError("принять можно только готовое предложение")
        out = dict(p["outcome"] or {})
        if p["kind"] == "rule":
            res = self.tools.add_rule(p["text"], mode or out.get("mode") or "auto")
            q = self.tools.regenerate("regenerate", f"предложение №{pid}")
            self._say(pid, "constraints", f"Добавил правило №{res['number']}: {res['understood']}.")
            self._say(pid, "watcher", "Поставил перегенерацию с тёплым стартом." if q.get("queued") else q.get("note", ""))
            out.update(rule_number=res["number"])
        else:
            res = self.tools.publish_draft(f"Предложение №{pid}: {p['text']}")
            self._say(pid, "publisher", f"Опубликовал версию v{res['version']}: затронуто групп — {res['groups_affected']}.")
            out.update(version=res["version"])
        self.store.update_proposal(pid, status="accepted", outcome=out)
        return self.get(pid)

    def reject(self, pid: int) -> dict:
        p = self.get(pid)
        if p["status"] in ("accepted", "rejected", "discussing"):
            raise RuntimeError("это предложение нельзя отклонить сейчас")
        out = dict(p["outcome"] or {})
        undone = self._undo_moves(out.get("moves") or [])
        if undone:
            self._say(pid, "scheduler", f"Вернул черновик как было: отменено переносов — {undone}.")
        self.store.update_proposal(pid, status="rejected", outcome={**out, "moves": []})
        return self.get(pid)

    # --- discussion --------------------------------------------------------------------------------
    def _start(self, pid: int, text: str, wait: bool) -> None:
        self.store.update_proposal(pid, status="discussing")
        t = threading.Thread(target=self._discuss, args=(pid, text), name=f"proposal-{pid}", daemon=True)
        self.threads[pid] = t
        t.start()
        if wait:
            t.join()

    def _say(self, pid: int, agent: str, text: str, actions: list | None = None, provider: str | None = None) -> None:
        self.store.add_proposal_msg(pid, agent, "agent", text, actions, provider)
        log = self.chat._logger()
        log.log("message", agent, to="operator", task="proposal", intent="result", payload={"proposal": pid, "text": text[:240]})
        log.close()

    def _handoff(self, pid: int, src: str, dst: str, task: str) -> None:
        log = self.chat._logger()
        log.log("message", src, to=dst, task=task, payload={"proposal": pid})
        log.close()

    def _discuss(self, pid: int, text: str) -> None:
        with self._lock:
            try:
                p = self.store.proposal(pid)
                kind = p["kind"]
                if not kind:
                    kind, note = self._classify(p["text"], p["context"] or [])
                    self.store.update_proposal(pid, kind=kind)
                    self._say(pid, "intake", note)
                if kind == "rule":
                    self._handoff(pid, "intake", "constraints", "оценить правило")
                    self._rule_round(pid, text)
                else:
                    self._handoff(pid, "intake", "scheduler", "подготовить переносы")
                    self._move_round(pid, text)
            except Exception as exc:  # the thread must never die silently
                self._say(pid, "system", f"Обсуждение прервалось: {type(exc).__name__}: {exc}")
                self.chat._logger().log("error", "proposals", msg=str(exc), trace=traceback.format_exc(limit=4))
                self.store.update_proposal(pid, status="needs_input")

    def _classify(self, text: str, context: list[dict]) -> tuple[str, str]:
        if not context and not MOVE_WORDS.search(text):
            try:
                pr = self.tools.preview_rule(text)
            except Exception:  # noqa: BLE001 - no dataset or parser trouble: treat as a change
                pr = {}
            if pr.get("understood"):
                return "rule", (f"Это общее правило: {pr['understood']}. Передаю агенту «Ограничения» — "
                                "он оценит его, а решение примете вы.")
        what = f" ({len(context)} занят.)" if context else ""
        return "move", (f"Это изменение конкретных занятий{what}. Передаю Планировщику: он подготовит переносы "
                        "в черновике, Критик проверит, а опубликуете вы.")

    def _thread_history(self, pid: int, agent: str) -> list[dict]:
        hist = []
        for m in self.store.proposal_msgs(pid)[:-1]:
            if m["role"] == "user":
                hist.append({"role": "user", "text": m["text"]})
            elif m["agent"] == agent:
                hist.append({"role": "assistant", "text": m["text"]})
        # the Messages API wants user / assistant turns to alternate
        out: list[dict] = []
        for h in hist:
            if out and out[-1]["role"] == h["role"]:
                out[-1] = {"role": h["role"], "text": out[-1]["text"] + "\n\n" + h["text"]}
            else:
                out.append(h)
        return out[1:] if out and out[0]["role"] == "assistant" else out

    def _context_lines(self, context: list[dict]) -> str:
        lines = []
        for c in context[:12]:
            lines.append(f"- группа {c.get('group')}: «{c.get('course')}», {DAY_RU[int(c.get('day', 0))]} {c.get('time', '')}, "
                         f"ауд. {c.get('room', '')}")
        return "Выбранные занятия:\n" + "\n".join(lines) if lines else ""

    def _draft_moves(self) -> list[dict]:
        try:
            return self.tools.editor.state()["moves"]
        except FileNotFoundError:
            return []

    def _move_round(self, pid: int, text: str) -> None:
        p = self.store.proposal(pid)
        before = {m["ts"] for m in self._draft_moves()}
        first = len(self.store.proposal_msgs(pid)) <= 2
        prompt = (f"Предложение оператора №{pid}: «{p['text']}».\n{self._context_lines(p['context'] or [])}\n"
                  if first else f"Оператор отвечает по предложению №{pid}: «{text}».\n")
        prompt += ("Подготовь конкретные изменения в черновике (move_session), сначала посмотри варианты (move_options или "
                   "suggest_moves). Ничего не публикуй — решение примет оператор. Если без нарушений нельзя — объясни "
                   "почему и предложи 1–3 альтернативы. Отвечай коротко.")
        reply, actions, prov = self.chat.run_agent("scheduler", prompt, self._thread_history(pid, "scheduler"),
                                                   offline=lambda: self._offline_move(p))
        self._say(pid, "scheduler", reply, actions, prov["label"])
        new = [m for m in self._draft_moves() if m["ts"] not in before]
        out = dict(self.store.proposal(pid)["outcome"] or {})
        moves = (out.get("moves") or []) + [{"ts": m["ts"], "label": m["label"]} for m in new]
        out["moves"] = moves
        if moves:
            self._handoff(pid, "scheduler", "validator", "проверить черновик")
            v = self.tools.validate()
            st = self.tools.draft_status()
            self._say(pid, "validator", summarize("validate", v, True) + (f" Штраф черновика: {st['score']:.1f}."
                                                                         if st.get("score") is not None else ""))
            out["hard"] = v["hard_violations"]
            status = "ready" if v["hard_violations"] == 0 else "needs_input"
        else:
            status = "needs_input"
        self.store.update_proposal(pid, status=status, outcome=out)

    def _offline_move(self, p: dict) -> str:
        """Without Claude: for each selected class take the best feasible slot (lowest penalty)."""
        done, notes = [], []
        for c in (p["context"] or [])[:6]:
            try:
                opts = self.tools.move_options(c["group"], int(c["day"]), str(c.get("time", ""))[:5])
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{c.get('group')}: {exc}")
                continue
            if not opts["best"]:
                notes.append(f"{c['group']} «{c['course']}»: допустимых слотов нет ({', '.join(opts['blocked_by'])})")
                continue
            b = opts["best"][0]
            try:
                r = self.tools.move_session(c["group"], int(c["day"]), str(c.get("time", ""))[:5], b["day"], b["time"], b["room"])
                done.append(f"{r['moved']} (штраф {b['delta']:+d})")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{c['group']}: {exc}")
        if not p["context"]:
            return ("Без выбранных занятий офлайн-правила не могут подобрать перенос. Выберите занятия в расписании "
                    "(«Предложить изменение») или напишите точно: «перенеси IT-2301 Пн 08:00 на Ср 11:00».")
        parts = (["Подготовил в черновике:", *[f"- {d}" for d in done]] if done else []) + notes
        return "\n".join(parts) or "Вариантов не нашёл."

    def _rule_round(self, pid: int, text: str) -> None:
        p = self.store.proposal(pid)
        pr = self.tools.preview_rule(p["text"])
        prompt = (f"Оператор предлагает правило: «{p['text']}». Как ты его понимаешь, не противоречит ли оно правилам вуза, "
                  "жёстким или мягким его сделать (мягким может быть только окно времени) и почему. Ничего не добавляй — "
                  "решение примет оператор. Коротко." if len(self.store.proposal_msgs(pid)) <= 2
                  else f"Оператор отвечает по правилу «{p['text']}»: «{text}». Коротко.")
        reply, actions, prov = self.chat.run_agent(
            "constraints", prompt, self._thread_history(pid, "constraints"), tools=["preview_rule", "list_rules"],
            offline=lambda: (f"Понял так: {pr['understood']}. " if pr.get("understood") else "Офлайн-парсер не распознал фразу. ")
            + ("Предлагаю мягкое правило — штраф вместо запрета." if pr.get("hard") is False
               else "Предлагаю жёсткое правило." if pr.get("understood") else ""))
        self._say(pid, "constraints", reply, actions, prov["label"])
        out = dict(self.store.proposal(pid)["outcome"] or {})
        out.update(understood=pr.get("understood"), mode="soft" if pr.get("hard") is False else "hard" if pr.get("understood") else "auto",
                   softable=pr.get("softable"), unknown=pr.get("unknown") or [])
        self.store.update_proposal(pid, status="needs_input" if out["unknown"] else "ready", outcome=out)

    def _undo_moves(self, moves: list[dict]) -> int:
        """Undo this proposal's moves while they are the last ones of the draft."""
        mine, n = {m["ts"] for m in moves}, 0
        while mine:
            cur = self._draft_moves()
            if not cur or cur[-1]["ts"] not in mine:
                break
            self.tools.editor.undo()
            mine.discard(cur[-1]["ts"])
            n += 1
        return n
