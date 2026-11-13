"""Opt-in schema-2 orchestration. Importing this module has no side effects."""


_BINDING_FIELDS = ('server', 'artist_id', 'device_id', 'token_file', 'outbox_dir',
                   'ca_file', 'allow_lan_http')


def _binding(config):
    return tuple(config.get(key, False if key == 'allow_lan_http' else None)
                 for key in _BINDING_FIELDS)


class BoundResolver:
    """Refresh approved paths, never rebind a collector/outbox to another actor."""
    def __init__(self, config, config_path, *, loader=None):
        from .managed_setup import load_config
        self.binding = _binding(config)
        self.config_path = config_path
        self.loader = loader or load_config

    def __call__(self, script_path):
        from .context import ContextResolver
        current = self.loader(self.config_path)
        if (not current.get('enabled', False) or
                _binding(current) != self.binding):
            raise ValueError('reporting binding changed; restart required')
        resolver = ContextResolver(current.get('mappings', []))
        context = resolver(script_path)
        if context is not None:
            from pathlib import Path
            path = Path(script_path)
            if path.exists() or path.is_symlink():
                real_context = resolver(str(path.resolve()))
                if (real_context is None or any(real_context[key] != context[key]
                        for key in ('job_id', 'shot_id'))):
                    return None
        return context


class Runtime:
    """Own one collector and uploader; wait quietly for a recognised script."""
    def __init__(self, *, nuke, identity, resolve, sink, uploader, installer, outbox=None):
        self.nuke = nuke
        self.identity = identity
        self.resolve = resolve
        self.sink = sink
        self.uploader = uploader
        self.outbox = outbox
        self.installer = installer
        self.adapter = None
        self.enabled = False
        self.last_error = None
        self._hooks = []
        self._adapter_cleanup_pending = False
        self._uploader_cleanup_pending = False

    def _context(self):
        return self.resolve(str(self.nuke.root().name() or ''))

    def _attach(self):
        if not self.enabled:
            return
        if self.adapter is not None:
            if self.adapter.installed:
                return
            if self._adapter_cleanup_pending:
                self.last_error = 'AdapterCleanupPending'
                return
            collector = self.adapter.collector
            collector.retry_pending()
            if collector.blocked:
                try:
                    collector.flush('runtime_recovery')
                except Exception as exc:
                    self.last_error = type(exc).__name__
                    return
            if collector.pending_count:
                self.last_error = 'PendingCollectorData'
                return
            self.adapter = None
        try:
            if getattr(self.nuke, '_nuke_time_calc_adapter', None) is not None:
                self.last_error = 'CollectorAlreadyInstalled'
                return
            context = self._context()
            if context is None:
                return
            self.adapter = self.installer(enabled=True, identity=self.identity,
                context_resolver=self._context, emit=self.sink, schema_version=2,
                nuke_module=self.nuke)
            self._adapter_cleanup_pending = self.adapter is not None
            self.last_error = None
        except Exception as exc:
            # A constructor may retain an inert adapter when its rollback also
            # fails. Adopt only the installation carrying our exact resolver
            # and sink, never an unrelated local diagnostic collector.
            retained = getattr(self.nuke, '_nuke_time_calc_adapter', None)
            if (retained is not None and not getattr(retained, 'installed', True)
                    and getattr(retained, 'context_resolver', None) == self._context
                    and getattr(getattr(retained, 'collector', None), 'emit', None) == self.sink):
                self.adapter = retained
                self._adapter_cleanup_pending = True
            self.last_error = type(exc).__name__

    def enable(self):
        if self.enabled:
            return self
        if self._hooks or self._adapter_cleanup_pending or self._uploader_cleanup_pending:
            self.disable()
            if self._hooks or self._adapter_cleanup_pending or self._uploader_cleanup_pending:
                return self
        self.enabled = True
        try:
            for suffix in ('OnScriptLoad', 'OnScriptSave'):
                callback = self._on_context_event
                getattr(self.nuke, 'add' + suffix)(callback)
                self._hooks.append((suffix, callback))
            self._uploader_cleanup_pending = True
            if self.uploader.start() is False:
                # A bounded stop cannot kill an in-flight upload. Never enable
                # collection with the old worker still committed to exiting.
                self.disable()
                self.last_error = 'UploaderStopping'
                return self
            self.last_error = None
            self._attach()
        except Exception:
            self.disable()
            raise
        return self

    def _on_context_event(self, *args):
        try:
            self._attach()
        except Exception as exc:
            self.last_error = type(exc).__name__


    def disable(self):
        self.enabled = False
        errors = []
        try:
            for target in reversed(self._hooks[:]):
                suffix, callback = target
                try:
                    getattr(self.nuke, 'remove' + suffix)(callback)
                except Exception as exc:
                    errors.append(type(exc).__name__)
                else:
                    self._hooks.remove(target)
        finally:
            try:
                if self._adapter_cleanup_pending:
                    try:
                        self.adapter.uninstall()
                    except Exception as exc:
                        errors.append(type(exc).__name__)
                    else:
                        self._adapter_cleanup_pending = False
            finally:
                if self._uploader_cleanup_pending:
                    try:
                        self._uploader_cleanup_pending = self.uploader.stop(timeout=0) is False
                        if self._uploader_cleanup_pending:
                            errors.append('UploaderStopping')
                    except Exception as exc:
                        errors.append(type(exc).__name__)
                if errors:
                    self.last_error = errors[0]

    def status(self):
        state = self.adapter.status() if self.adapter else {}
        coverage = ('disabled' if not self.enabled else
                    'collecting' if self.adapter and state.get('running', True)
                    and not state.get('blocked', False) else
                    'paused' if self.adapter else 'unresolved_script')
        queue = {}
        if self.outbox is not None:
            try:
                queue = self.outbox.status()
            except Exception:
                queue = {'available': False, 'code': 'outbox_error'}
        return {'enabled': self.enabled,
                'coverage': coverage, 'outbox': queue,
                'collector': state, 'upload': self.uploader.status(),
                'last_error': self.last_error}


def build_runtime(config, nuke, config_path=None, *, uploader_factory=None, installer=None):
    from .outbox import Outbox
    from .transport import Uploader
    from .nuke_adapter import install
    outbox = Outbox(config['outbox_dir'], server=config['server'],
                    artist_id=config['artist_id'], device_id=config['device_id'])
    upload_options = dict(token_file=config['token_file'])
    if 'ca_file' in config:
        upload_options['ca_file'] = config.get('ca_file')
    if 'allow_lan_http' in config:
        upload_options['allow_lan_http'] = config['allow_lan_http']
    uploader = (uploader_factory or Uploader)(outbox, **upload_options)
    return Runtime(nuke=nuke, identity='device:' + config['device_id'],
        resolve=BoundResolver(config, config_path), sink=outbox.put,
        uploader=uploader, installer=installer or install, outbox=outbox)


class Bootstrap:
    def __init__(self, nuke, config_path, config_loader, runtime_factory):
        self.nuke = nuke
        self.config_path = config_path
        self.config_loader = config_loader
        self.runtime_factory = runtime_factory
        self.runtime = None
        self.last_error = None
        self.cancelled = False
        self._setup_timer = None

    def enable(self):
        try:
            config = self.config_loader(self.config_path)
            if not config.get('enabled', False):
                # Missing automatic setup is a waiting state, not a user stop.
                self._disable_runtime()
                return None
            if self.runtime is None:
                self.runtime = self.runtime_factory(config, self.nuke, self.config_path)
            self.runtime.enable()
            self.last_error = None
        except Exception as exc:
            self.last_error = type(exc).__name__
            self.nuke.tprint('shotbox time reporting: ' + self.last_error)
        return self.runtime

    def deferred_enable(self):
        if not self.cancelled:
            self.enable()
            if self.runtime is not None and getattr(self.runtime, 'enabled', False):
                if self._setup_timer is not None:
                    self._setup_timer.stop()

    def disable(self):
        self.cancelled = True
        if self._setup_timer is not None:
            self._setup_timer.stop()
        self._disable_runtime()

    def _disable_runtime(self):
        if self.runtime is not None:
            try:
                self.runtime.disable()
            except Exception as exc:
                self.last_error = type(exc).__name__

    def status(self):
        state = self.runtime.status() if self.runtime else {'coverage': 'disabled'}
        return dict(state, last_error=self.last_error or state.get('last_error'))

    def print_status(self):
        # Only explicit menu actions display dialogs. Never serialize runtime
        # payloads or exception text: they may contain paths or credentials.
        try:
            state = self.status()
        except Exception as exc:
            state = {'last_error': type(exc).__name__}
        detail = ('Reporting is not active. Waiting for Shotbox automatic setup. '
                  'Start Shotbox with a linked artist.')
        if state.get('coverage') == 'collecting':
            collector = state.get('collector', {})
            if (state.get('enabled') and collector.get('installed')
                    and collector.get('running') is True and not collector.get('blocked')):
                detail = 'Reporting is collecting estimated comp time for the verified shot.'
            else:
                detail = 'Reporting activity is not confirmed. Check the reporting configuration.'
        elif state.get('coverage') == 'unresolved_script':
            detail = ('Reporting is waiting for a verified shot. Open a Nuke script '
                      'under a verified shot path.')
        elif state.get('coverage') == 'paused':
            detail = 'Reporting is paused; the collector is not active.'
        error = state.get('last_error') or state.get('collector', {}).get('last_error')
        if error:
            import re
            # Stored errors are class names / fixed lifecycle codes, not messages.
            if not isinstance(error, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', error):
                error = 'ReportingError'
            detail = ('Reporting is paused. ' if state.get('coverage') == 'paused' else '') + (
                'Reporting error: ' + error + '. Reporting may not be active.')
            if self.cancelled:
                detail += ' Cleanup may be incomplete; restart Nuke if this persists.'
        message = ('shotbox estimated comp time\n' + detail + '\n'
                   'Shotbox manages reporting automatically; start Shotbox with a linked artist if reporting remains inactive.')
        getattr(self.nuke, 'message', self.nuke.tprint)(message)


def register(nuke_module=None, config_path=None, *, config_loader=None,
             runtime_factory=None, defer=None):
    if nuke_module is None:
        import nuke as nuke_module
    if not getattr(nuke_module, 'GUI', False):
        return None
    existing = getattr(nuke_module, '_shotbox_time_reporting', None)
    if existing is not None:
        return existing
    if config_loader is None:
        from .managed_setup import load_config
        config_loader = load_config
    controller = Bootstrap(nuke_module, config_path, config_loader, runtime_factory or build_runtime)
    main = nuke_module.menu('Nuke')
    top = main.findItem('shotbox') or main.addMenu('shotbox')
    menu = top.findItem('nuke_time_calc') or top.addMenu('nuke_time_calc')
    if menu.findItem('Reporting status') is None:
        menu.addCommand('Reporting status', controller.print_status)
    nuke_module._shotbox_time_reporting = controller
    if defer is None:
        from .local_test import _qt_modules
        qt_core, _, _ = _qt_modules()
        timer = qt_core.QTimer()
        timer.setInterval(30000)
        timer.timeout.connect(controller.deferred_enable)
        controller._setup_timer = timer
        timer.start()
        defer = lambda callback: qt_core.QTimer.singleShot(0, callback)
    defer(controller.deferred_enable)
    return controller

