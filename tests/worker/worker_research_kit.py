"""Extra helpers for tests/worker/test_worker_research.py (step 20b item 5: research runs in the worker), kept in a
separate module from worker_kit.py (an existing file this testing agent does not touch, per its brief) so that
established, shared file stays untouched by this concurrent slice of work. Imports worker_kit.py by name, the same
way every test file of this folder already does.
"""
import worker_kit as kit


def make_source_act(message, **fields):
    """A minimal, valid InterventionAct a research ModerationRun's `source_act` can point at: an ordinary
    `offer_research` act on a `live` run of `message`'s conversation. Nothing about its content matters to the
    worker (claiming/reaping/dispatch are all `kind`-driven, not content-driven); only that it is a real, saved
    InterventionAct row, since `ModerationRun.validate_rules()` requires one for a `kind="research"` run."""
    from moderation.models import InterventionAct, ModerationRun

    live_run = ModerationRun.objects.create(
        conversation=message.conversation, trigger_message=message, snapshot_seq=message.seq_no, kind="live",
        status="done",
    )
    values = dict(
        run=live_run, order=1, act_type="offer_research", tone="neutral",
        text="An independent check could be requested for this claim.", addressee="all", subject="none",
        validity="valid",
    )
    values.update(fields)
    return InterventionAct.objects.create(**values)


def research_run_on(message, **fields):
    """A research ModerationRun on `message` (pending by default, `source_act` made fresh unless one is given) --
    the `kind="research"` analogue of worker_kit.py's own `run_on`."""
    from moderation.models import ModerationRun

    act = fields.pop("source_act", None) or make_source_act(message)
    values = dict(
        conversation=message.conversation, trigger_message=message, snapshot_seq=message.seq_no,
        kind="research", source_act=act,
    )
    values.update(fields)
    return ModerationRun.objects.create(**values)


class ResearchStub:
    """Stands in for moderation.research.run_research. Mirrors worker_kit.PipelineStub's shape and bookkeeping, kept
    separate so a test can install both stubs at once and check each kind was routed to the right one."""

    def __init__(self, behaviour=None):
        self.calls = []
        self.behaviour = behaviour or kit.finish("done")

    def __call__(self, run, *args, **kwargs):
        self.calls.append(run.pk)
        return self.behaviour(run)

    @property
    def pks(self):
        return list(self.calls)


def install_research(monkeypatch, behaviour=None):
    """Replace research.run_research everywhere the worker might look it up (module attribute AND, defensively, a
    same-named attribute on moderation.worker, in case it was imported with `from ... import run_research` instead
    of the module-level `from moderation import research` the current code uses) -- the same double-patch worker_kit
    .install_pipeline already uses for moderation.pipeline.run_moderation, for the same reason."""
    import moderation.research
    import moderation.worker

    stub = ResearchStub(behaviour)
    monkeypatch.setattr(moderation.research, "run_research", stub)
    monkeypatch.setattr(moderation.worker, "run_research", stub, raising=False)
    return stub
