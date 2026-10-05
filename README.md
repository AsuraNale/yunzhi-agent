# Yunzhi Agent

[中文版](README.zh-CN.md)

Yunzhi Agent is a research assistant for policy, public-sector and industrial-economics researchers. It runs inside OpenAI Codex.

You give it a research commission in plain Chinese. It takes the project through four steps — clarify the task, collect materials, outline, write and deliver — and asks you to confirm at each key point. The result is a Word document and a web page in which every claim is traced to a source.

> **Status:** v0.1.
> - Tested on Windows 11 with the Codex desktop app 26.930 and the Codex CLI 0.160, using GPT-6.1 Sol and GPT-5.6 Terra.
> - The assistant talks to users in Chinese.

## What it does

- **Four steps:** 明确任务 (clarify the task) → 收集资料 (collect materials) → 拟定提纲 (outline) → 撰写交付 (write and deliver).
- **Confirmations:**
  - You confirm the task plan, the materials summary and the outline, then the delivery. A project that skips the outline step has two confirmations before delivery.
  - Each confirmation is an option card: a native Codex card, or a numbered card in chat.
  - Nothing moves on without your answer.
- **Two kinds of brief:** one that tests a stated judgment against agreed criteria, or one that reviews the situation without a verdict.
- **Evidence cards:**
  - Each source is read and recorded as a card with a verbatim excerpt, its stance (supports, against, background) and a locator.
  - Counter-evidence is searched for explicitly.
  - When data are missing, the assistant stops and asks how you want to handle the gap.
- **Independent review:**
  - Before delivery, a separate reviewer agent checks the draft paragraph by paragraph against the sources.
  - It is started without access to the writing conversation.
- **Checks by script:** citation, consistency and formatting checks run as scripts. The Word and web versions are generated from the same draft.
- **Progress view:** a progress page in Codex's right-hand pane, which the scripts keep up to date.

## How it is built

| Path | What it holds |
|---|---|
| `AGENTS.md` | The working rules (shared by Codex and WorkBuddy) |
| `.agents/skills/` | Eight skills: the entry point (`yunzhi`), the four steps (`task-planner`, `evidence-card`, `outline-cocreate`, `cite-trace`), the review (`review-module`), option cards (`yunzhi-card`) and progress (`yunzhi-progress`) |
| `agent-tools/` | Python scripts the agent calls: projects, cards, progress, review, fetching, link checks, archiving, and the guard hook |
| `toolkit/` | The research toolkit: the specification (`04-正本规格-v1.md`), checkers, HTML and Word renderers, confirmation records |
| `wording/` | A glossary and a banned-terms list that keep internal jargon out of what users see |
| `.codex/config.toml` | Workspace settings: Windows sandbox mode, native option cards, and plugins this workspace doesn't need turned off |
| `.codex/hooks.json` | The guard hooks (below) |
| `tests/` | Tests and a mutation check |

Counting, formatting and checking are done by scripts, not left to the model.

## Guard hooks

Once you approve the workspace's hooks in Codex, `agent-tools/hook.py` enforces the following:

- **Your words:** every message you send is logged verbatim by the hook. Quotes of your words on cards, and your answers to cards, are checked against that log.
- **Cards:**
  - A native option card can only be shown if it matches a card prepared by the scripts.
  - The non-blocking `request_user_input_async` tool is refused.
- **Confirmation records:** these can only be written through the card script. Direct writes to records or to approval fields are refused.
- **Review:** the reviewer agent must be started with `fork_turns: "none"`, and the main agent cannot send it messages.
- **Waiting for you:** while a choice card is still waiting for your answer, the assistant cannot move the project to the next step.
- **Final reply:** the last reply of each turn is scanned for internal file links, skill names, jargon and talk about how the tools work. A reply that fails is sent back once to be rephrased.

If the hooks are not approved, they don't run, and the assistant relies on the written rules alone.

## Requirements

- Windows 10 or 11 (tested on Windows 11). For macOS, see Limitations.
- Python 3.10 or later (tested with 3.12), available through the `py` launcher.
- The Python packages in `toolkit/requirements.txt`: PyYAML, python-docx and pypdf.
- The OpenAI Codex desktop app (tested with 26.930) or the Codex CLI (tested with 0.160). From 26.930 on, the Windows Start menu lists the desktop app as "ChatGPT".

## Getting started

1. Clone this repository.
2. Install the packages: `py -m pip install -r toolkit/requirements.txt`.
3. Open the folder as a project in the Codex desktop app, and trust it when asked. Project settings and hooks only load in trusted folders.
4. Start a new chat and approve the hooks: in the message box, click the **Review hooks** icon (its tooltip shows how many hooks are waiting for approval) and choose **Allow all**. Codex doesn't open this review on its own, and the hooks don't run until you approve them.
5. Send `新建项目：<topic>。<what you need, for example length and audience>`, and attach any materials you have.
6. Answer the cards as they come.
7. To continue a project later, start a new chat and say `接着做`.

Projects live in `projects/<name>/`, which git ignores. Finished files are in `projects/<name>/out/`.

## Answering cards

- Click an option, or reply in chat with its number.
- To add a remark, put it after the number, for example `2，时间段改成从 2021 年开始`.
- A remark on its own doesn't confirm anything; reply with the number when you're ready.
- If a native card goes unanswered, Codex closes it after about three minutes. Just reply with the option number.

## Tests

```
py -X utf8 -m pytest -q
py -X utf8 tests/mutation_check.py
py -X utf8 -m pytest -q toolkit/scripts
```

- The first command runs the workspace tests, about 700 of them.
- The second breaks each check on purpose and confirms that a test catches it.
- The third runs the toolkit's own tests.

## Limitations

- **Platforms:**
  - Only Windows has been tested. `.codex/hooks.json` calls `py`, so on macOS the hooks won't run until the command is changed to `python3`.
  - WorkBuddy support (`.codebuddy/skills/`, kept in sync by `agent-tools/sync_skills.py`) is in place but untested.
- **What the hooks can't do:**
  - The final-reply check only sees the last reply of a turn, and only after it has been shown. Messages between tool calls depend on the written rules.
  - The shell guards match obvious patterns. They catch mistakes; they are not a security boundary.
- **Codex behaviour:**
  - The inline progress snapshot in chat is turned off, because Codex 26.930 showed it as raw text. The right-hand pane shows progress instead.
  - Choosing a model in a new Codex chat also changes your global default model. That is Codex behaviour, not this project's.

## Related

- [Yunzhi-Ann v0.1](https://github.com/AsuraNale/yunzhi-ann): the same research pipeline, packaged as skills and plugins for DeepSeek Harness.

## License

MIT. See [LICENSE](LICENSE).
