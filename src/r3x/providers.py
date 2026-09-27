"""Provider declarations and explicit, synchronous lifecycle ownership.

Adapters must return from lifecycle methods and release partial allocations in
close(). Worker/task cancellation and its deadlines belong to the audible slice.
"""
from dataclasses import dataclass
from importlib import metadata
from typing import Protocol
from .contracts import ProviderFailure


@dataclass(frozen=True)
class Capability:
    name: str
    provider: str
    reason: str
    model: str | None = None
    dependencies: tuple[str, ...] = ()  # Installed distribution names; never imports.
    resources: tuple[str, ...] = ()
    required: bool = True
    requires: tuple[str, ...] = ()  # Earlier live providers; skip dependents when unavailable.
    startup: str = "explicit start"
    shutdown: str = "cancel then close; release partial allocations"


class Provider(Protocol):
    capability: Capability

    def start(self) -> None: ...
    def cancel(self) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class LifecycleResult:
    capability: str
    operation: str
    outcome: str
    error_type: str | None = None
    reason: str | None = None


def dependency_status(capability: Capability) -> dict[str, str | None]:
    result = {}
    for name in capability.dependencies:
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = None
    return result


class ProviderOwner:
    def __init__(self, providers: tuple[Provider, ...] = ()) -> None:
        names = [provider.capability.name for provider in providers]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate capability owners")
        self.providers = providers
        self.results: list[LifecycleResult] = []
        self._active: list[Provider] = []
        self.state = "new"

    def _call(self, provider: Provider, operation: str) -> bool:
        try:
            getattr(provider, operation)()
        except Exception as exc:
            # Arbitrary provider exception text may contain credentials/requests.
            self.results.append(LifecycleResult(provider.capability.name, operation,
                                                "failed", type(exc).__name__,
                                                str(exc) if isinstance(exc, ProviderFailure) else None))
            return False
        self.results.append(LifecycleResult(provider.capability.name, operation, "ok"))
        return True

    def _release(self, provider: Provider) -> None:
        self._call(provider, "cancel")
        self._call(provider, "close")

    def start(self, on_required_failure=None) -> bool:
        if self.state != "new":
            raise RuntimeError("A lifecycle owner can only start once")
        self.state = "starting"
        try:
            for provider in self.providers:
                ready_names = {p.capability.name for p in self._active}
                if any(name not in ready_names for name in provider.capability.requires):
                    self.results.append(LifecycleResult(provider.capability.name,'start',
                        'skipped',reason='RequiredCapabilityUnavailable'))
                    continue
                if any(value is None for value in dependency_status(provider.capability).values()):
                    self.results.append(LifecycleResult(provider.capability.name, "start",
                                                        "failed", "MissingDependency"))
                    ready = False
                else:
                    self._active.append(provider)  # Include partial startup in cleanup.
                    ready = self._call(provider, "start")
                    if not ready:
                        self._release(provider)
                        self._active.pop()
                if not ready and provider.capability.required:
                    if on_required_failure:
                        try:
                            on_required_failure()
                        except Exception as exc:
                            self.results.append(LifecycleResult('startup_notice','announce','failed',type(exc).__name__))
                    self.close()
                    return False
        except BaseException:
            self.close()
            raise
        self.state = "ready"
        return True

    def cancel(self) -> None:
        for provider in reversed(self._active):
            self._call(provider, "cancel")

    def close(self) -> None:
        while self._active:
            self._release(self._active.pop())
        self.state = "cleanup_failed" if any(
            r.outcome == "failed" and r.operation in {"cancel", "close"}
            for r in self.results
        ) else "closed"
