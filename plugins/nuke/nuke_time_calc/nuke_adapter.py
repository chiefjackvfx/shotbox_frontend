"""Opt-in Nuke adapter. Importing this module never installs callbacks."""


from nuke_time_calc.core import Collector


class Adapter:
    def __init__(self, collector, nuke, context_resolver, qt_core, app):
        self.collector = collector
        self.nuke = nuke
        self.context_resolver = context_resolver
        self.bindings = []
        self.last_error = None
        self.installed = True
        self.app = app
        self.qt_core = qt_core
        self._focus_callback = self._guard(self.on_focus)
        self._tick_callback = self._guard(self.on_tick)
        self.timer = None
        self._focus_connected = self._tick_connected = False
        self._timer_stop_pending = False
        try:
            app.applicationStateChanged.connect(self._focus_callback)
            self._focus_connected = True
            self.on_focus(app.applicationState())
            self.timer = qt_core.QTimer()
            self._timer_stop_pending = True
            self.timer.setInterval(1000)
            self.timer.timeout.connect(self._tick_callback)
            self._tick_connected = True
            self.last_checkpoint = collector.monotonic()
            self.timer.start()
        except Exception:
            # Construction is transactional even when start partially activates
            # the timer before raising. Disconnect each initialized resource.
            try:
                self.uninstall()
            except Exception:
                pass  # Keep the original setup error; uninstall retains targets.
            raise

    def uninstall(self):
        self.installed = False
        errors = []
        # Each resource has its own retry marker. A failed disconnect must not
        # skip collector shutdown or cause already-removed hooks to be retried.
        for flag, owner, method, args in (
                ('_timer_stop_pending', self.timer, 'stop', ()),
                ('_tick_connected', getattr(self.timer, 'timeout', None),
                 'disconnect', (self._tick_callback,)),
                ('_focus_connected', self.app.applicationStateChanged,
                 'disconnect', (self._focus_callback,))):
            if getattr(self, flag):
                try:
                    getattr(owner, method)(*args)
                except Exception as exc:
                    errors.append(exc)
                else:
                    setattr(self, flag, False)
        for target in reversed(self.bindings[:]):
            suffix, callback = target
            try:
                getattr(self.nuke, "remove" + suffix)(callback)
            except Exception as exc:
                errors.append(exc)
            else:
                self.bindings.remove(target)
        try:
            self.collector.stop("uninstall")
        except BufferError as exc:
            self.last_error = type(exc).__name__
        except Exception as exc:
            errors.append(exc)
        if errors:
            self.last_error = type(errors[0]).__name__
            # Keep ownership until cleanup succeeds, including failed setup.
            self.nuke._nuke_time_calc_adapter = self
            raise errors[0]
        if getattr(self.nuke, "_nuke_time_calc_adapter", None) is self:
            delattr(self.nuke, "_nuke_time_calc_adapter")

    def status(self):
        c = self.collector
        return dict(installed=self.installed, running=c.running,
                    session_id=c.session_id, sequence=c.sequence,
                    pending_count=c.pending_count, blocked=c.blocked,
                    unsealed_comp_seconds=c.comp_seconds,
                    focused=c.focused, rendering=c.rendering,
                    last_error=self.last_error or c.last_error)

    def _fail_closed(self, exc):
        self.last_error = type(exc).__name__
        try:
            self.collector.stop("adapter_error")
        except BufferError:
            pass  # core retains both the full queue and the unsealed interval

    def _guard(self, callback):
        def guarded(*args, **kwargs):
            if not self.installed:
                return
            try:
                return callback(*args, **kwargs)
            except Exception as exc:
                self._fail_closed(exc)
        return guarded

    def bind(self, suffix, callback):
        callback = self._guard(callback)
        getattr(self.nuke, "add" + suffix)(callback)
        self.bindings.append((suffix, callback))

    def on_knob_changed(self):
        # Playback/UI notifications are observations, not edits. Do not let a
        # looping Viewer renew the production allowance on every frame.
        if self.collector.schema_version == 2:
            knob = self.nuke.thisKnob()
            node = self.nuke.thisNode()
            if knob is None or node is None:
                return
            name, kind = knob.name(), node.Class()
            if (name in ('frameChanged', 'updateUI') or kind == 'Viewer' or
                    (kind == 'Root' and name == 'frame')):
                return
        self.collector.activity()

    def on_before_render(self):
        self.collector.set_rendering(True)

    def on_after_render(self):
        self.collector.set_rendering(False)

    def on_focus(self, state):
        self.collector.set_focus(state == self.qt_core.Qt.ApplicationActive)

    def on_tick(self):
        # Settle the entire uncertain interval as render BEFORE clearing it.
        # QTimer may have been blocked, and cancellation may omit AfterRender.
        now = self.collector.tick()
        if self.collector.rendering and not self.nuke.executing():
            self.collector.set_rendering(False)
        if now < self.last_checkpoint:
            self.last_checkpoint = now  # new monotonic epoch; don't wait for the old clock
        if now - self.last_checkpoint >= 60.0:
            if self.collector.running:
                self.collector.flush()
            else:
                self.collector.retry_pending()
            self.last_checkpoint = now

    def refresh_context(self):
        if not self.installed:
            return False
        try:
            context = self.context_resolver()
            self.collector.switch_context(context)
            self.collector.start()
        except Exception as exc:
            self.last_error = type(exc).__name__
            self.collector.stop("context_unresolved")
            return False
        self.last_error = None
        return True

    def on_close(self):
        self.collector.stop("script_close")

    def on_load(self):
        self.collector.stop("script_load")
        self.refresh_context()

    def on_save(self):
        if self.refresh_context():
            self.collector.flush("script_save")


def install(*, enabled=False, identity=None, context_resolver=None, emit=None,
            nuke_module=None, qt_core=None, app=None, **clock_options):
    if not enabled:
        return None
    if not callable(context_resolver):
        raise ValueError("an explicit context_resolver callable is required")
    collector = Collector(identity=identity, context=context_resolver(),
                          emit=emit, **clock_options)
    if nuke_module is None:
        import nuke as nuke_module
    if getattr(nuke_module, "_nuke_time_calc_adapter", None) is not None:
        raise RuntimeError("nuke_time_calc already installed; uninstall it first")
    if qt_core is None or app is None:
        try:
            from PySide2 import QtCore, QtWidgets
        except ImportError:
            from PySide6 import QtCore, QtWidgets
        qt_core = QtCore if qt_core is None else qt_core
        app = QtWidgets.QApplication.instance() if app is None else app
    for suffix in ("KnobChanged", "OnUserCreate", "OnDestroy", "OnScriptLoad",
                   "OnScriptSave", "OnScriptClose", "BeforeRender", "AfterRender"):
        for prefix in ("add", "remove"):
            if not callable(getattr(nuke_module, prefix + suffix, None)):
                raise ValueError("unsupported Nuke callback: " + prefix + suffix)
    adapter = Adapter(collector, nuke_module, context_resolver, qt_core, app)
    try:
        adapter.bind("KnobChanged", adapter.on_knob_changed)
        adapter.bind("OnUserCreate", collector.activity)
        adapter.bind("OnDestroy", collector.activity)
        adapter.bind("OnScriptClose", adapter.on_close)
        adapter.bind("OnScriptLoad", adapter.on_load)
        adapter.bind("OnScriptSave", adapter.on_save)
        adapter.bind("BeforeRender", adapter.on_before_render)
        adapter.bind("AfterRender", adapter.on_after_render)
        collector.start()
    except Exception:
        adapter.uninstall()
        raise
    nuke_module._nuke_time_calc_adapter = adapter
    return adapter
