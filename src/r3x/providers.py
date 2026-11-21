"""Provider declarations and explicit lifecycle ownership.

Adapters must return from lifecycle methods and release partial allocations in
close(). Worker/task cancellation and its deadlines belong to the audible slice.

Startup is one main sequence plus groups started beside it (owner, November 20): the
models load while the servos wake instead of after them. start() still returns only once
everything has been started, and shutdown order never depends on which thread was first.
"""
from dataclasses import dataclass
from importlib import metadata
import threading
import time
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
    began: float | None = None    # Starts only: seconds after startup began,
    seconds: float | None = None  # and how long this start took.


@dataclass(frozen=True)
class Alongside:
    """Providers started in order on a thread of their own while the main sequence carries on."""
    providers: tuple
    after: Provider                  # Begins once this main-sequence provider has been started (or tried).
    before: Provider | None = None   # The main sequence waits for the group before starting this one.


class Preload:
    """Loads models ahead of the providers that own them, while something slower is going on
    (owner, November 20: the camera-side models during the servo wake). Each model still belongs
    to its provider, whose own start then finds it loaded and which closes it as before. A model
    that fails here fails this (optional) step by name; everything loaded here is then let go, and
    each owner loads its own in its turn and reports its own failure. Whatever no provider went on
    to use, because the camera never started, is closed here."""
    def __init__(self, name: str, models: tuple, reason: str):
        self.models = tuple(models)
        self.capability = Capability(name, "early load", reason, required=False,
                                     shutdown="close any model its provider did not")

    def start(self) -> None:
        for model in self.models:
            try:
                model.start()
            except ProviderFailure as exc:
                raise ProviderFailure(type(model).__name__ + ':' + str(exc)) from None

    def cancel(self) -> None:
        pass

    def close(self) -> None:
        for model in self.models:
            model.close()


def dependency_status(capability: Capability) -> dict[str, str | None]:
    result = {}
    for name in capability.dependencies:
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = None
    return result


class ProviderOwner:
    def __init__(self, providers: tuple[Provider, ...] = (), alongside: tuple[Alongside, ...] = ()) -> None:
        """providers: every provider, in the main sequence's order; closing runs it in reverse.
        alongside: the groups among them that start beside the main sequence instead of in it."""
        names = [provider.capability.name for provider in providers]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate capability owners")
        self.providers = providers
        self._groups = tuple(group for group in alongside if group.providers)
        self._order = {id(provider): index for index, provider in enumerate(providers)}
        self._group_of = {id(provider): group for group in self._groups for provider in group.providers}
        grouped = [provider for group in self._groups for provider in group.providers]
        if len(self._group_of) != len(grouped) or any(id(provider) not in self._order for provider in grouped):
            raise ValueError("A provider started alongside must be listed once and owned here")
        for group in self._groups:
            marks = [group.after] + ([group.before] if group.before is not None else [])
            if any(id(mark) not in self._order or id(mark) in self._group_of for mark in marks):
                raise ValueError("Groups begin after, and are awaited before, main-sequence providers")
            if group.before is not None and self._order[id(group.before)] <= self._order[id(group.after)]:
                raise ValueError("A group cannot be awaited before it begins")
        self._by_name = dict(zip(names, providers))
        self._settled = {name: threading.Event() for name in names}   # Its start has been tried or skipped.
        self._halt = threading.Event()   # A required provider failed or startup was interrupted: start nothing more.
        self._lock = threading.Lock()
        self._began = None
        self.results: list[LifecycleResult] = []
        self._active: list[Provider] = []
        self.state = "new"

    def _call(self, provider: Provider, operation: str, began: float | None = None) -> bool:
        def timing():
            if began is None:
                return ()
            return (round(began - self._began, 3), round(time.monotonic() - began, 3))
        try:
            getattr(provider, operation)()
        except Exception as exc:
            # Arbitrary provider exception text may contain credentials/requests.
            self.results.append(LifecycleResult(provider.capability.name, operation,
                                                "failed", type(exc).__name__,
                                                str(exc) if isinstance(exc, ProviderFailure) else None, *timing()))
            return False
        self.results.append(LifecycleResult(provider.capability.name, operation, "ok", None, None, *timing()))
        return True

    def _release(self, provider: Provider) -> None:
        self._call(provider, "cancel")
        self._call(provider, "close")

    def _requirements_met(self, provider: Provider) -> bool:
        group = self._group_of.get(id(provider))
        for name in provider.capability.requires:
            other = self._by_name.get(name)
            # Started on another thread: wait until it has been tried. Only an earlier provider counts.
            if (other is not None and self._group_of.get(id(other)) is not group
                    and self._order[id(other)] < self._order[id(provider)]):
                while not self._settled[name].wait(.05):
                    if self._halt.is_set():
                        return False
        with self._lock:
            ready = {p.capability.name for p in self._active}
        return all(name in ready for name in provider.capability.requires)

    def _start(self, provider: Provider) -> None:
        """One provider's start with its checks. A required failure halts the rest of startup."""
        capability = provider.capability
        try:
            if not self._requirements_met(provider):
                self.results.append(LifecycleResult(capability.name, 'start',
                    'skipped', reason='RequiredCapabilityUnavailable'))
                return
            if any(value is None for value in dependency_status(capability).values()):
                self.results.append(LifecycleResult(capability.name, "start", "failed", "MissingDependency"))
                ready = allocated = False
            else:
                with self._lock:
                    self._active.append(provider)  # Include partial startup in cleanup.
                ready, allocated = self._call(provider, "start", time.monotonic()), True
            if not ready and capability.required:
                self._halt.set()   # Before the release below: nothing new starts in the meantime.
            if not ready and allocated:
                self._release(provider)
                with self._lock:
                    self._active.remove(provider)
        finally:
            self._settled[capability.name].set()

    def _start_group(self, group: Alongside) -> None:
        provider = group.providers[0]
        try:
            for provider in group.providers:
                if self._halt.is_set():
                    return
                self._start(provider)
        except BaseException as exc:
            # Nothing a group does may end silently: the rest of startup stops and is unwound.
            self.results.append(LifecycleResult(provider.capability.name, 'start', 'failed', type(exc).__name__))
            self._halt.set()

    def start(self, on_required_failure=None) -> bool:
        if self.state != "new":
            raise RuntimeError("A lifecycle owner can only start once")
        self.state = "starting"
        self._began = time.monotonic()
        threads = {}
        told = []
        def tell():
            if told or not on_required_failure:
                return
            told.append(True)
            try:
                on_required_failure()
            except Exception as exc:
                self.results.append(LifecycleResult('startup_notice','announce','failed',type(exc).__name__))
        try:
            for provider in self.providers:
                if id(provider) in self._group_of:
                    continue   # Its group starts it.
                for index, group in enumerate(self._groups):
                    if group.before is provider and index in threads:
                        threads[index].join()
                if self._halt.is_set():
                    break
                self._start(provider)
                for index, group in enumerate(self._groups):
                    if group.after is provider and not self._halt.is_set():
                        threads[index] = threading.Thread(target=self._start_group, args=(group,), daemon=True,
                            name='r3x-start-' + group.providers[0].capability.name)
                        threads[index].start()
            if self._halt.is_set():
                tell()   # Said at once; the groups still loading are waited out below.
            for thread in threads.values():
                thread.join()
            if self._halt.is_set():
                tell()
                self.close()
                return False
        except BaseException:
            self._halt.set()
            for thread in threads.values():
                thread.join()
            self.close()
            raise
        self.state = "ready"
        return True

    def timings(self) -> dict:
        """How startup went, for the session log: when each start began and how long it took."""
        steps = {r.capability: [r.began, r.seconds] for r in self.results
                 if r.operation == 'start' and r.seconds is not None}
        return {'seconds': round(max((began + seconds for began, seconds in steps.values()), default=0.), 3),
                'steps': steps}

    def _started(self, names=None) -> list:
        """Active providers in closing order: the main sequence reversed, whichever thread started them."""
        with self._lock:
            return [p for p in reversed(self.providers)
                    if p in self._active and (names is None or p.capability.name in names)]

    def cancel(self) -> None:
        for provider in self._started():
            self._call(provider, "cancel")

    def _close(self, providers) -> None:
        for provider in providers:
            with self._lock:
                self._active.remove(provider)
            self._release(provider)

    def close_only(self, names) -> None:
        """Release selected providers (hardware) while stuck workers keep the rest."""
        self._close(self._started(names))

    def close(self) -> None:
        self._close(self._started())
        self.state = "cleanup_failed" if any(
            r.outcome == "failed" and r.operation in {"cancel", "close"}
            for r in self.results
        ) else "closed"
