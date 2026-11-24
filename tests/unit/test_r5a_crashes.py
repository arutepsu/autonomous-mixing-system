"""Process-crash evidence at audit/run-store boundaries; labelled transport, never a provider call."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_claude_config import config

from autonomous_mixing.adapters.journal.decision_exchange import SqliteDecisionExchangeStore
from autonomous_mixing.adapters.journal.sqlite_run import SqliteRunJournal
from autonomous_mixing.core.loop.restart import classify_run
from autonomous_mixing.core.records.decision_exchange import DecisionCallKey, ExchangeStatus
from autonomous_mixing.core.records.run_restart import RunInterrupted
from autonomous_mixing.drivers.decision_evidence import ConfiguredDecisionEvidenceSource

ROOT = Path(__file__).resolve().parents[2]
CHILD = r"""
import os,sys
from pathlib import Path
sys.path.insert(0,'tests/unit')
from test_r5a_coordinator_gate import bound_harness,execute
from test_coordinator import Identities
from test_claude_config import config
from test_claude_decision_maker import Transport
from autonomous_mixing.adapters.journal.sqlite_run import SqliteRunJournal
from autonomous_mixing.adapters.decision_agent.claude import ClaudeDecisionMaker
from autonomous_mixing.core.records.acceptance import AcceptedReference
from autonomous_mixing.core.records.decision_evidence import DecisionEvidenceVerified
from autonomous_mixing.core.records.run_trace import OperationObserved,OperationKind
from autonomous_mixing.drivers.decision_evidence import ConfiguredDecisionEvidenceSource
root,point=Path(sys.argv[1]),sys.argv[2]
h,audit=bound_harness(root)
real=SqliteRunJournal.create(root/'run.sqlite',h.request.run_id,AcceptedReference(h.request.initial_state,0))
class CrashTransport(Transport):
 def send(self,*a,**kw):
  if point=='pending':os._exit(77)
  return super().send(*a,**kw)
class CrashJournal:
 def __getattr__(self,name):return getattr(real,name)
 def append(self,event):
  result=real.append(event)
  if point=='proof' and isinstance(event.payload,DecisionEvidenceVerified):os._exit(77)
  if point=='observation' and isinstance(event.payload,OperationObserved) and event.payload.operation is OperationKind.DECIDE:os._exit(77)
  return result
class CrashEvidence(ConfiguredDecisionEvidenceSource):
 def verify(self,*a):
  if point=='completed':os._exit(77)
  return super().verify(*a)
h.journal=CrashJournal()
maker=ClaudeDecisionMaker(config=config(),transport=CrashTransport(),audit=audit,identities=Identities(),clock=h.clock)
evidence=CrashEvidence(binding=h.request.provider_binding,config=config(),audit=audit)
execute(h,maker,evidence)
raise RuntimeError('crash point not reached')
"""


@pytest.mark.parametrize("point", ["pending", "completed", "proof", "observation"])
def test_crash_releases_locks_but_never_authorizes_resume_or_resend(tmp_path, point):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    child = subprocess.run(
        [sys.executable, "-c", CHILD, str(tmp_path), point],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == 77, child.stdout + child.stderr
    run = SqliteRunJournal.open_read_only(tmp_path / "run.sqlite")
    audit = SqliteDecisionExchangeStore.open(tmp_path / "audit.sqlite", run_id=run.run_id, max_record_bytes=2000000)
    try:
        snapshot = run.snapshot().value
        binding = snapshot.events[0].payload.provider_binding
        entry = audit.read(DecisionCallKey(run.run_id, 1)).record
        assert entry.status is (ExchangeStatus.PENDING if point == "pending" else ExchangeStatus.COMPLETED)
        assert isinstance(classify_run(snapshot, None), RunInterrupted)
        verifier = ConfiguredDecisionEvidenceSource(binding=binding, config=config(), audit=audit)
        problem = verifier.verify_run(snapshot)
        assert (problem is None) == (point == "observation")
        # No DecisionMaker/transport is constructed by reopening or verifying evidence.
    finally:
        run.close()
        audit.close()
