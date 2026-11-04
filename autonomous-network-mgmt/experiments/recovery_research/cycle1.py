"""Cycle 1: newly implemented forecast baseline without causal stability gating."""

from recovery_completion.controller import CompletionController
from recovery_lifecycle.contracts import Settings


def create_controller() -> CompletionController:
    return CompletionController(Settings(), require_settled=False)

