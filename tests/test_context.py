"""Context assembly: sections, priorities, quotas, repair and the report (PLAN 6)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from fakes import ScriptedModel
from myagent.agent.compaction import ModelSummarizer, boundary_for_turns, compact_session
from myagent.agent.context import (
    DEFAULT_CONVERSATION_RATIO,
    MISSING_TOOL_RESULT,
    SECTION_CONVERSATION,
    SECTION_MEMORY,
    SECTION_QUERY,
    SECTION_RAG,
    SECTION_SUMMARY,
    SECTION_SYSTEM,
    SECTION_TOOLS,
    CompactionReport,
    ContextBudget,
    ContextItem,
    ContextManager,
    ContextReport,
    ContextRequest,
    ContextSection,
    ContextWindowExceeded,
    SectionedContextManager,
    SectionReport,
)
from myagent.agent.token_budget import messages_tokens, truncate_to_tokens
from myagent.agent.types import Message, ToolCallRequest
from myagent.models.base import ContextWindowExceeded as ProviderContextWindowExceeded
from myagent.models.base import LLMError, LLMResponse
from myagent.session.base import Session

MARKER = "w" * 80  # 20 tokens per marker, so a section's size is easy to read
FLOOR = "the system prompt plus the query"  # what no budget can ever trim


def manager(tmp_path, **kwargs: object) -> SectionedContextManager:
    return SectionedContextManager(tmp_path, **kwargs)


def probe(tmp_path, budget: int, **kwargs: object) -> ContextRequest:
    return ContextRequest(user_input="hello", budget_tokens=budget, **kwargs)


def stuffed(*, budget: int, turns: int = 8, items: int = 6) -> ContextRequest:
    """A request whose conversation, memory, RAG and tool sections are all full."""
    history = [
        message
        for index in range(turns)
        for message in (
            Message.user(f"question {index} {MARKER}"),
            Message.assistant(f"answer {index} {MARKER}"),
        )
    ]
    return ContextRequest(
        user_input=f"the newest question {MARKER}",
        history=history,
        memories=[
            ContextItem(f"memory {i} {MARKER}", reference=f"m{i}", score=i / 10)
            for i in range(items)
        ],
        rag_chunks=[
            ContextItem(f"chunk {i} {MARKER}", reference=f"d{i}#0", score=i / 10)
            for i in range(items)
        ],
        tools=[
            {"function": {"name": f"tool_{i}", "description": f"does thing {i} " + MARKER}}
            for i in range(items)
        ],
        budget_tokens=budget,
    )


def tool_ids(messages) -> list[str]:
    return [m.tool_call_id for m in messages if m.role == "tool" and m.tool_call_id]


def announced_ids(messages) -> list[str]:
    return [call.id for m in messages if m.role == "assistant" for call in m.tool_calls]


def test_system_prompt_describes_identity_and_runtime(tmp_path):
    prompt = manager(tmp_path).system_prompt()

    assert "You are MyAgent" in prompt
    assert "current time:" in prompt
    assert f"workspace root: {tmp_path}" in prompt
    assert "tools" in prompt


def test_build_orders_system_history_and_the_new_message(tmp_path):
    history = [Message.user("first"), Message.assistant("ok")]

    bundle = manager(tmp_path).build(ContextRequest(user_input="second", history=history))

    assert [message.role for message in bundle.messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert bundle.messages[-1].content == "second"
    # The system prompt and the replayed history are already stored: the turn
    # starts at its own user message.
    assert bundle.transcript_start == 3


def test_build_without_history_still_produces_a_user_turn(tmp_path):
    bundle = manager(tmp_path).build(ContextRequest(user_input="hello"))

    assert [message.role for message in bundle.messages] == ["system", "user"]
    assert bundle.transcript_start == 1


def test_sections_carry_priority_and_required_flags(tmp_path):
    request = ContextRequest(
        user_input="hello",
        history=[Message.user("before"), Message.assistant("after")],
        summary="earlier: the user prefers Python",
        memories=[ContextItem("prefers Python")],
        rag_chunks=[ContextItem("GraphRAG text", reference="abc123#0", score=0.9)],
        tools=[{"function": {"name": "calculator", "description": "arithmetic"}}],
    )

    sections = manager(tmp_path).sections(request)

    assert [section.name for section in sections] == [
        SECTION_SYSTEM,
        SECTION_QUERY,
        SECTION_CONVERSATION,
        SECTION_SUMMARY,
        SECTION_MEMORY,
        SECTION_RAG,
        SECTION_TOOLS,
    ]
    assert [section.priority for section in sections] == [0, 1, 2, 3, 4, 5, 6]
    assert [section.required for section in sections] == [
        True,
        True,
        False,
        False,
        False,
        False,
        False,
    ]


def test_optional_sections_are_omitted_when_empty(tmp_path):
    sections = manager(tmp_path).sections(ContextRequest(user_input="hi"))

    assert [section.name for section in sections] == [SECTION_SYSTEM, SECTION_QUERY]


def test_retrieved_context_joins_the_leading_system_block(tmp_path):
    bundle = manager(tmp_path).build(
        ContextRequest(
            user_input="what did I say?",
            memories=[ContextItem("prefers Python"), ContextItem("studies RAG", reference="m1")],
            rag_chunks=[ContextItem("chunk text", reference="doc1#3")],
            summary="we discussed chunk sizes",
        )
    )

    system, *rest = bundle.messages
    assert system.role == "system"
    assert "Relevant memory:" in system.content
    assert "- prefers Python" in system.content
    assert "- studies RAG [m1]" in system.content
    assert "Retrieved documents:" in system.content
    assert "- chunk text [doc1#3]" in system.content
    assert "Earlier conversation summary:\nwe discussed chunk sizes" in system.content
    assert [message.role for message in rest] == ["user"]
    # Memory, RAG and the summary land in the system prompt, so only the user turn
    # is new.
    assert bundle.transcript_start == len(bundle.messages) - 1


def test_the_tool_block_lists_names_and_descriptions(tmp_path):
    bundle = manager(tmp_path).build(
        ContextRequest(
            user_input="hi",
            tools=[
                {"function": {"name": "calculator", "description": "arithmetic"}},
                {"name": "raw_shape", "description": "a bare function mapping"},
            ],
        )
    )

    system = bundle.messages[0]
    assert "Available tools:" in system.content
    assert "- calculator: arithmetic" in system.content
    assert "- raw_shape: a bare function mapping" in system.content


def test_section_tokens_cover_text_and_messages(tmp_path):
    text_section = ContextSection(SECTION_SYSTEM, 0, True, "abcd")
    message_section = ContextSection(
        SECTION_CONVERSATION,
        2,
        True,
        [Message.user("你好"), Message.assistant("ok")],
    )
    tool_call_section = ContextSection(
        SECTION_CONVERSATION,
        2,
        True,
        [Message.assistant(None, tool_calls=[ToolCallRequest("call_1", "duck", {"text": "hi"})])],
    )

    assert text_section.estimated_tokens() == 1
    assert message_section.estimated_tokens() == messages_tokens(message_section.content)
    assert message_section.estimated_tokens() > 2
    assert tool_call_section.estimated_tokens() > 1
    assert ContextSection(SECTION_MEMORY, 4, False, "").estimated_tokens() == 0


def test_the_bundle_reports_its_estimated_size(tmp_path):
    bundle = manager(tmp_path).build(ContextRequest(user_input="hello"))

    assert bundle.estimated_tokens == sum(section.estimated_tokens() for section in bundle.sections)
    assert bundle.estimated_tokens > 0


def test_a_request_inside_the_budget_is_returned(tmp_path):
    bundle = manager(tmp_path).build(probe(tmp_path, 100_000))

    assert bundle.messages[-1].content == "hello"
    assert bundle.report is not None
    assert bundle.report.dropped == 0
    assert bundle.report.summary_line() == (
        f"budget=100000 used={bundle.estimated_tokens} dropped=0"
    )


def test_an_over_budget_request_raises_with_both_numbers(tmp_path):
    with pytest.raises(ContextWindowExceeded) as caught:
        manager(tmp_path).build(probe(tmp_path, 1))

    assert caught.value.budget == 1
    assert caught.value.estimated > 1
    assert "LLM_CONTEXT_WINDOW" in str(caught.value)
    # Same vocabulary as Phase 2's provider-side error, so one handler covers both.
    assert isinstance(caught.value, ProviderContextWindowExceeded)


def test_the_required_sections_are_never_trimmed(tmp_path):
    """Only the system prompt and the query survive a budget that fits nothing else."""
    bare = manager(tmp_path).build(ContextRequest(user_input="hello"))
    floor = bare.estimated_tokens  # system + query, and there is nothing to cut

    assert manager(tmp_path).build(probe(tmp_path, floor)).estimated_tokens == floor
    with pytest.raises(ContextWindowExceeded):
        manager(tmp_path).build(probe(tmp_path, floor - 1))


def test_a_provider_token_counter_overrides_the_estimate(tmp_path):
    counted = ContextSection(SECTION_SYSTEM, 0, True, "abcd").estimated_tokens()

    def counter(messages) -> int:
        return len(messages) * 100

    bundle = manager(tmp_path, tokens=counter).build(ContextRequest(user_input="hello"))

    assert bundle.estimated_tokens == 200
    assert bundle.estimated_tokens != counted
    assert bundle.report is not None
    assert bundle.report.used == 200


def test_a_silent_token_counter_falls_back_to_the_estimate(tmp_path):
    bundle = manager(tmp_path, tokens=lambda messages: None).build(
        ContextRequest(user_input="hello")
    )

    assert bundle.estimated_tokens == sum(section.estimated_tokens() for section in bundle.sections)


# --------------------------------------------------------------------------
# PLAN 6.1: the trimming order
# --------------------------------------------------------------------------


def test_priority_order(tmp_path):
    """A squeezed request gives up tools first, then RAG, then memory, then history.

    The budget is twice the untrimmable floor (system + query), which is the range
    where the four trimmable sources saturate their quotas and still overflow: the
    tool block pays first, RAG pays next, memory pays third, and the conversation —
    the smallest priority number of the four — keeps something.
    """
    bare = manager(tmp_path).build(ContextRequest(user_input="hello")).estimated_tokens
    budget = 2 * bare
    request = stuffed(budget=budget)
    request = ContextRequest(
        user_input=request.user_input,
        history=request.history,
        memories=request.memories,
        rag_chunks=request.rag_chunks,
        tools=request.tools,
        budget_tokens=budget,
    )

    bundle = manager(tmp_path, budget=ContextBudget(input_tokens=budget)).build(request)
    used = {entry.name: entry.used for entry in bundle.report.sections}
    before = {entry.name: entry.dropped + entry.used for entry in bundle.report.sections}

    assert used[SECTION_TOOLS] == 0 and before[SECTION_TOOLS] > 0
    assert used[SECTION_RAG] == 0 and before[SECTION_RAG] > 0
    assert 0 < used[SECTION_MEMORY] < before[SECTION_MEMORY]
    assert used[SECTION_CONVERSATION] > 0
    assert bundle.estimated_tokens <= budget


def test_the_quota_order_is_read_from_the_budget(tmp_path):
    budget = ContextBudget(input_tokens=1000)

    assert budget.quota(SECTION_CONVERSATION) == 350
    assert budget.quota(SECTION_RAG) == 350
    assert budget.quota(SECTION_MEMORY) == 200
    assert budget.quota(SECTION_SUMMARY) == 100
    assert budget.quota(SECTION_TOOLS) == 100
    assert budget.quota(SECTION_SYSTEM) == 100
    assert ContextBudget().quota(SECTION_CONVERSATION) is None
    assert budget.with_input_tokens(None).quota(SECTION_CONVERSATION) is None


def test_the_budget_rejects_impossible_shares():
    with pytest.raises(ValueError, match="input_tokens must be positive"):
        ContextBudget(input_tokens=0)
    with pytest.raises(ValueError, match=r"conversation_ratio must be within 0\.\.1"):
        ContextBudget(conversation_ratio=1.5)
    with pytest.raises(ValueError, match=r"rag_ratio must be within 0\.\.1"):
        ContextBudget(rag_ratio=0.0)


def test_budget_clipping(tmp_path):
    """Four stuffed sources: the result fits the budget and stays structurally valid."""
    request = stuffed(budget=1200)

    bundle = manager(tmp_path, budget=ContextBudget(input_tokens=1200)).build(request)

    assert bundle.estimated_tokens <= 1200
    assert bundle.report is not None
    assert bundle.report.used <= 1200
    assert bundle.report.input_tokens == 1200
    # Structure: one system block, the query last, no orphan tool traffic.
    assert bundle.messages[0].role == "system"
    assert bundle.messages[-1].role == "user"
    assert tool_ids(bundle.messages) == announced_ids(bundle.messages)
    # Every section was reported, with its quota and its usage.
    quotas = {entry.name: entry.budget for entry in bundle.report.sections}
    assert quotas[SECTION_CONVERSATION] == 420
    assert quotas[SECTION_RAG] == 420
    assert quotas[SECTION_MEMORY] == 240
    assert quotas[SECTION_TOOLS] == 120
    assert all(
        entry.used <= entry.budget
        for entry in bundle.report.sections
        if entry.budget is not None and not entry.required
    )


def test_the_report_names_every_downgrade(tmp_path):
    request = stuffed(budget=900, turns=20, items=20)

    bundle = manager(tmp_path, budget=ContextBudget(input_tokens=900)).build(request)

    actions = {entry.name: entry.action for entry in bundle.report.sections}
    assert "compacted" in actions[SECTION_CONVERSATION]
    assert actions[SECTION_RAG].startswith("dropped ")
    assert actions[SECTION_MEMORY].startswith("dropped ")
    assert actions[SECTION_TOOLS].startswith("dropped ")
    # The report keeps the sections in priority order, not in the order they were cut.
    assert [entry.name for entry in bundle.report.trimmed_sections] == [
        SECTION_CONVERSATION,
        SECTION_MEMORY,
        SECTION_RAG,
        SECTION_TOOLS,
    ]
    assert bundle.report.dropped > 0
    assert "dropped" in bundle.report.summary_line()


def test_the_lowest_score_item_is_dropped_first(tmp_path):
    """Both over-quota sources give up their worst entries, never their best ones."""
    ranked = [
        ContextItem(f"memory {i} {MARKER}", reference=f"m{i}", score=1.0 - i / 10) for i in range(6)
    ]
    chunks = [
        ContextItem(f"chunk {i} {MARKER}", reference=f"d{i}#0", score=1.0 - i / 10)
        for i in range(6)
    ]
    request = ContextRequest(user_input="hi", memories=ranked, rag_chunks=chunks, budget_tokens=300)

    clipped = manager(tmp_path, budget=ContextBudget(input_tokens=300)).build(request)

    system = clipped.messages[0].content
    kept_memory = [i for i in range(6) if f"memory {i} " in system]
    kept_chunks = [i for i in range(6) if f"chunk {i} " in system]

    # Scores descend with the index, so what survives is always a prefix: the best
    # entries are kept and the weakest ones are the ones that go.
    assert 0 < len(kept_memory) < 6
    assert kept_memory == list(range(len(kept_memory)))
    assert 0 < len(kept_chunks) < 6
    assert kept_chunks == list(range(len(kept_chunks)))
    assert "Relevant memory:" in system and "Retrieved documents:" in system


def test_the_summary_is_truncated_to_its_quota(tmp_path):
    request = ContextRequest(user_input="hi", summary=MARKER * 20, budget_tokens=1000)

    clipped = manager(tmp_path, budget=ContextBudget(input_tokens=1000)).build(request)
    kept = next(s for s in clipped.sections if s.name == SECTION_SUMMARY)

    assert kept.estimated_tokens() <= 100
    actions = {entry.name: entry.action for entry in clipped.report.sections}
    assert actions[SECTION_SUMMARY] == "truncated the archived summary"


def test_a_summary_that_already_fits_is_left_alone(tmp_path):
    request = ContextRequest(
        user_input="hi", summary="we agreed on 800-token chunks", budget_tokens=1000
    )

    bundle = manager(tmp_path, budget=ContextBudget(input_tokens=1000)).build(request)

    actions = {entry.name: entry.action for entry in bundle.report.sections}
    assert actions[SECTION_SUMMARY] == ""
    assert bundle.messages[0].content.endswith("we agreed on 800-token chunks")
    assert bundle.report.dropped == 0


def test_the_fit_loop_degrades_from_the_largest_priority_down(tmp_path):
    """With every share at 100% the quotas cut nothing, so the whole cascade is the fit
    loop's: tools, RAG, memory, summary, then the oldest turn (PLAN 6.1), one item at a
    time and each step named in the report.
    """
    base = replace(stuffed(budget=1, turns=4), summary=MARKER * 5)
    budget = messages_tokens(base.history) + 20  # room for the conversation, nothing else
    shares = ContextBudget(
        input_tokens=budget,
        conversation_ratio=1.0,
        rag_ratio=1.0,
        memory_ratio=1.0,
        other_ratio=1.0,
    )

    bundle = manager(tmp_path, budget=shares).build(replace(base, budget_tokens=budget))

    actions = {entry.name: entry.action for entry in bundle.report.sections}
    assert "dropped a tool description to fit the budget" in actions[SECTION_TOOLS]
    assert "dropped a document chunk to fit the budget" in actions[SECTION_RAG]
    assert "dropped a memory item to fit the budget" in actions[SECTION_MEMORY]
    assert "truncated the archived summary to fit the budget" in actions[SECTION_SUMMARY]
    assert "dropped the oldest turn to fit the budget" in actions[SECTION_CONVERSATION]
    assert bundle.estimated_tokens <= budget
    # Nothing was over its own share, so the automatic compaction never fired.
    assert bundle.compaction is not None and bundle.compaction.compacted is False


def test_a_request_without_a_budget_is_never_trimmed(tmp_path):
    request = stuffed(budget=1)
    unbudgeted = ContextRequest(
        user_input=request.user_input,
        history=list(request.history),
        memories=list(request.memories),
        rag_chunks=list(request.rag_chunks),
        tools=list(request.tools),
    )

    bundle = manager(tmp_path).build(unbudgeted)

    assert [section.name for section in bundle.sections] == [
        SECTION_SYSTEM,
        SECTION_QUERY,
        SECTION_CONVERSATION,
        SECTION_MEMORY,
        SECTION_RAG,
        SECTION_TOOLS,
    ]
    assert bundle.report.dropped == 0
    assert bundle.report.input_tokens is None
    assert bundle.report.summary_line().startswith("budget=unlimited")


# --------------------------------------------------------------------------
# PLAN 6.3: structural repair
# --------------------------------------------------------------------------


def test_orphan_tool_repair(tmp_path):
    """Both malformed shapes are fixed: orphans go, missing results get a placeholder."""
    history = [
        Message.user("read the file"),
        Message.tool("ghost_call", "an orphan: no assistant ever announced this call"),
        Message.assistant(
            None,
            tool_calls=[
                ToolCallRequest("call_1", "read_file", {"path": "a.txt"}),
                ToolCallRequest("call_2", "read_file", {"path": "b.txt"}),
            ],
        ),
        Message.tool("call_1", "a.txt: hello"),
        Message.assistant("done"),
    ]

    bundle = manager(tmp_path).build(
        ContextRequest(user_input="thanks", history=history, budget_tokens=10_000)
    )

    assert "ghost_call" not in tool_ids(bundle.messages)
    assert tool_ids(bundle.messages) == ["call_1", "call_2"]
    assert announced_ids(bundle.messages) == ["call_1", "call_2"]
    placeholder = next(m for m in bundle.messages if m.tool_call_id == "call_2")
    assert placeholder.content == MISSING_TOOL_RESULT
    assert placeholder.name == "read_file"
    assert placeholder.role == "tool"
    actions = {entry.name: entry.action for entry in bundle.report.sections}
    assert "dropped 1 orphan tool result(s)" in actions[SECTION_CONVERSATION]
    assert "backfilled 1 missing tool result(s)" in actions[SECTION_CONVERSATION]


def test_a_tool_result_before_its_call_is_an_orphan(tmp_path):
    """A result that arrives before the call it answers is unusable, so it is dropped."""
    history = [
        Message.tool("call_1", "out of order"),
        Message.assistant(None, tool_calls=[ToolCallRequest("call_1", "echo", {})]),
    ]

    bundle = manager(tmp_path).build(
        ContextRequest(user_input="hi", history=history, budget_tokens=10_000)
    )

    assert tool_ids(bundle.messages) == ["call_1"]
    assert [m.content for m in bundle.messages[1:]] == [
        None,
        MISSING_TOOL_RESULT,
        "hi",
    ]


def test_a_well_formed_history_is_left_alone(tmp_path):
    history = [
        Message.user("read a.txt"),
        Message.assistant(None, tool_calls=[ToolCallRequest("call_1", "read_file", {})]),
        Message.tool("call_1", "a.txt: hello"),
        Message.assistant("it says hello"),
    ]

    bundle = manager(tmp_path).build(
        ContextRequest(user_input="thanks", history=history, budget_tokens=10_000)
    )

    assert [m.content for m in bundle.messages[1:5]] == [
        "read a.txt",
        None,
        "a.txt: hello",
        "it says hello",
    ]
    assert "orphan" not in (bundle.report.sections[2].action or "")


def test_repair_and_clipping_work_together(tmp_path):
    """A malformed transcript stays malformed-free even when the budget also cuts it."""
    history = [
        Message.user("old " + MARKER),
        Message.tool("ghost", "orphan " + MARKER),
        Message.assistant(None, tool_calls=[ToolCallRequest("c1", "echo", {})]),
        Message.tool("c1", "result " + MARKER),
        Message.user("new " + MARKER),
        Message.assistant("answer " + MARKER),
    ]

    bundle = manager(tmp_path, budget=ContextBudget(input_tokens=250)).build(
        ContextRequest(user_input="hi", history=history, budget_tokens=250)
    )

    assert tool_ids(bundle.messages) == announced_ids(bundle.messages)
    assert "ghost" not in tool_ids(bundle.messages)
    assert MISSING_TOOL_RESULT not in [m.content for m in bundle.messages]
    assert bundle.estimated_tokens <= 250
    assert bundle.compaction is not None and bundle.compaction.compacted is True


def test_a_backfill_that_no_longer_fits_is_refused(tmp_path):
    """The size is judged *after* the repair, so a placeholder is paid for, not hidden.

    PLAN 6.3 puts the two repair steps between the clipping and the check for
    exactly this reason: the backfilled tool result costs tokens, and a request
    that only fits once the repair is forgotten is a request the provider rejects.
    """
    request = ContextRequest(
        user_input="hi",
        history=[
            Message.user("read a.txt"),
            Message.assistant(None, tool_calls=[ToolCallRequest("call_1", "read_file", {})]),
        ],
    )
    # The size the fit loop starts from: the same request, before the repair runs.
    unrepaired = sum(section.estimated_tokens() for section in manager(tmp_path).sections(request))

    with pytest.raises(ContextWindowExceeded) as caught:
        manager(
            tmp_path, budget=ContextBudget(input_tokens=unrepaired, conversation_ratio=1.0)
        ).build(request)

    assert caught.value.budget == unrepaired
    assert caught.value.estimated > unrepaired


# --------------------------------------------------------------------------
# PLAN 6.4: compaction
# --------------------------------------------------------------------------


def test_compaction_reports_what_it_dropped(tmp_path):
    history = [Message.user(f"question {i} {MARKER}") for i in range(10)]
    report = manager(tmp_path, budget=ContextBudget(input_tokens=100)).compact(
        history, budget_tokens=100
    )

    # 100 * 0.35 = 35 tokens: only the newest question or two fit.
    assert report.compacted is True
    kept = len(history) - report.messages_removed
    assert 0 < kept < len(history)
    assert report.after_tokens == messages_tokens(history[-kept:]) <= 35
    assert report.turns_removed == report.messages_removed
    assert report.tokens_saved == report.before_tokens - report.after_tokens > 0
    assert report.boundary == 0


def test_compaction_keeps_a_whole_turn_together(tmp_path):
    history = [
        Message.user("one"),
        Message.assistant(None, tool_calls=[ToolCallRequest("c1", "echo", {})]),
        Message.tool("c1", MARKER),
        Message.assistant("done"),
        Message.user("two"),
    ]

    kept, report = manager(tmp_path)._compact(list(history), 12)

    assert report.compacted is True
    assert kept == [Message.user("two")]
    assert report.messages_removed == 4
    assert report.turns_removed == 1


def test_compaction_is_a_no_op_without_a_budget(tmp_path):
    report = manager(tmp_path).compact([Message.user("hello")])

    assert report == CompactionReport()
    assert report.compacted is False


def test_compaction_leaves_a_short_history_alone(tmp_path):
    history = [Message.user("hi")]

    report = manager(tmp_path, budget=ContextBudget(input_tokens=100_000)).compact(history)

    assert report.compacted is False
    assert report.tokens_saved == 0


def test_the_build_stage_reports_automatic_compaction(tmp_path):
    request = stuffed(budget=400, turns=10)

    bundle = manager(tmp_path, budget=ContextBudget(input_tokens=400)).build(request)

    assert bundle.compaction is not None
    assert bundle.compaction.compacted is True
    assert bundle.compaction.messages_removed > 0


async def test_the_model_summariser_cuts_its_answer_to_its_own_maximum():
    """One tool-free completion, capped at ``max_tokens`` (the 10% "other" share)."""
    model = ScriptedModel(LLMResponse(content=f"  {MARKER * 5}  "))
    summariser = ModelSummarizer(model, max_tokens=20)

    summary = await summariser.summarize([Message.user("what did we decide?")])

    assert summariser.model is model
    assert summary == MARKER  # 400 characters of "w" are 100 tokens; 20 tokens is 80
    prompt, transcript = model.requests[0][0]
    assert prompt.role == "system" and "checkpoint summary" in prompt.content
    assert transcript.role == "user"
    assert transcript.content.startswith("Transcript to compress:\nuser: what did we decide?")


async def test_the_model_summariser_refuses_an_empty_answer():
    """An empty summary would delete the conversation, so it fails the compaction."""
    model = ScriptedModel(LLMResponse(content="   "))

    with pytest.raises(LLMError, match="empty summary"):
        await ModelSummarizer(model).summarize([Message.user("hi")])


async def test_compaction_reads_the_tool_traffic_and_archives_every_turn(tmp_path):
    """``keep_recent_turns=0`` archives everything, tool calls included (PLAN 6.4)."""
    history = [
        Message.user("read a.txt"),
        Message.assistant(
            None, tool_calls=[ToolCallRequest("call_1", "read_file", {"path": "a.txt"})]
        ),
        Message.tool("call_1", "a.txt: hello", name="read_file"),
        Message.user("and b.txt"),
    ]
    model = ScriptedModel(LLMResponse(content="the agent read a.txt and was asked for b.txt"))
    session = Session(key="cli:test", messages=history, summary="an older checkpoint")

    result = await compact_session(session, ModelSummarizer(model), keep_recent_turns=0)

    transcript = model.requests[0][0][1].content
    assert 'assistant: called read_file({"path": "a.txt"})' in transcript
    assert "tool read_file: a.txt: hello" in transcript
    assert result.boundary == len(history)
    assert result.summary == "the agent read a.txt and was asked for b.txt"
    assert result.report.messages_removed == 4
    assert result.report.turns_removed == 2
    # Nothing is replayed and the summary is all that is left of the four messages.
    assert result.report.after_tokens < result.report.before_tokens
    assert result.report.tokens_saved == result.report.before_tokens - result.report.after_tokens


def test_the_boundary_lands_on_the_start_of_a_turn():
    history = [
        Message.user("one"),
        Message.assistant("a"),
        Message.user("two"),
        Message.assistant("b"),
        Message.user("three"),
        Message.assistant("c"),
    ]

    assert boundary_for_turns(history, 1) == 4
    assert boundary_for_turns(history, 2) == 2
    assert boundary_for_turns(history, 3) == 0
    assert boundary_for_turns(history, 9) == 0  # fewer turns than asked to keep
    assert boundary_for_turns(history, 0) == len(history)  # keep nothing verbatim


def test_a_turn_is_a_user_message_and_everything_up_to_the_next_one():
    from myagent.agent.context import _drop_turn_size

    history = [Message.user("one"), Message.tool("call_1", "result"), Message.user("two")]

    assert _drop_turn_size(history) == 2  # the tool result belongs to the first turn
    assert _drop_turn_size([]) == 0


# --------------------------------------------------------------------------
# The contract itself
# --------------------------------------------------------------------------


def test_the_sectioned_manager_satisfies_the_protocol(tmp_path):
    assert isinstance(manager(tmp_path), ContextManager)


def test_truncate_to_tokens_never_grows_the_text():
    assert truncate_to_tokens("", 5) == ""
    assert truncate_to_tokens("你好世界", 0) == ""
    assert truncate_to_tokens("你好世界", 2) == "你好"
    assert truncate_to_tokens("你好世界", 99) == "你好世界"
    assert truncate_to_tokens("a" * 100, 3) == "a" * 12


def test_the_report_can_be_built_from_sections_alone():
    report = ContextReport(
        sections=(
            SectionReport("system", 0, True, None, 10, 0, ""),
            SectionReport("rag", 5, False, 100, 20, 80, "dropped 2 document chunk(s)"),
        ),
        used=30,
        dropped=80,
    )

    assert [entry.name for entry in report.trimmed_sections] == ["rag"]
    assert (
        report.summary_line()
        == "budget=unlimited used=30 dropped=80 (rag: dropped 2 document chunk(s))"
    )


def test_the_default_conversation_ratio_is_the_plan_number():
    assert DEFAULT_CONVERSATION_RATIO == 0.35
    assert ContextBudget(input_tokens=100).conversation_ratio == 0.35


def test_the_workspace_section_reports_the_manager_budget(tmp_path):
    budget = ContextBudget(input_tokens=1234)

    assert manager(tmp_path, budget=budget).budget is budget
    assert manager(tmp_path).budget.input_tokens is None
