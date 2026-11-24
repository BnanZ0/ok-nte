"""Show configured GitHub repositories in the upstream plugin tab."""

from contextvars import ContextVar

_PATCH_INSTALLED = False


def install_plugin_repository_patch(repositories):
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED or not repositories:
        return

    from ok.core.plugins import github_repository
    from ok.ui.qt.common.config import cfg
    from ok.ui.qt.kit.buttons import Button
    from ok.ui.qt.kit.feedback import Pill
    from ok.ui.qt.plugins import PluginTab as plugin_page
    from PySide6.QtCore import QCoreApplication, QThreadPool, Slot

    configured = []
    for entry in repositories:
        if isinstance(entry, str):
            entry = {"url": entry}
        if not isinstance(entry, dict) or not isinstance(url := entry.get("url"), str):
            continue
        try:
            owner, name = github_repository(url)
        except ValueError:
            continue
        configured.append(
            (
                entry.get("name") or name,
                f"{owner}/{name}",
                entry.get("file_name"),
                entry.get("supported_languages"),
            )
        )
    if not configured:
        return

    # Collect sources already read from installed folders by the upstream refresh.
    installed_sources = ContextVar("installed_plugin_sources", default=None)
    read_plugin_source = plugin_page.read_plugin_source

    def collect_plugin_source(folder):
        source = read_plugin_source(folder)
        if source is not None and (sources := installed_sources.get()) is not None:
            sources.add(source["repository"].lower())
        return source

    class PluginTab(plugin_page.PluginTab):
        def __init__(self):
            locale = cfg.language.value.name()
            self._repositories = {}
            for name, repository, file_name, languages in configured:
                if not languages or locale in languages:
                    self._repositories.setdefault(repository.lower(), (name, repository, file_name))
            self._listed_plugin_ids = set()
            self._release_versions = {}
            self._version_pills = {}
            self._checking_release_versions = False
            super().__init__()

        def _empty_state(self):
            card = super()._empty_state()
            if self._repositories:
                card.hide()
            return card

        def _schedule_update_checks(self, entries):
            # Upstream has already merged installed entries and pending changes.
            self._listed_plugin_ids = {entry["file_name"] for entry in entries}
            super()._schedule_update_checks(entries)

        @Slot()
        def refresh(self):
            sources = set()
            token = installed_sources.set(sources)
            try:
                super().refresh()
            finally:
                installed_sources.reset(token)

            self._version_pills.clear()
            for key, (name, repository, file_name) in self._repositories.items():
                if key in sources or file_name in self._listed_plugin_ids:
                    continue
                version = self._release_versions.get(key)
                # PluginCard only creates the pill when given non-empty text.
                card = plugin_page.PluginCard(name, version or " ", self.plugins)
                pill = card.findChild(Pill)
                pill.setVisible(bool(version))
                self._version_pills[key] = pill
                card.add_note(self.tr("From GitHub: {}").format(repository))
                button = card.add_action(
                    Button(QCoreApplication.translate("ProjectCard", "Download"), "download")
                )
                button.setEnabled(not self._busy)
                button.clicked.connect(
                    lambda _checked=False, repo=repository: self._download_repository(repo)
                )
                self._update_buttons.append(button)
                self.plugins_layout.addWidget(card)

        def showEvent(self, event):
            super().showEvent(event)
            if self._checking_release_versions:
                return
            repositories = {
                key: self._repositories[key][1]
                for key in self._version_pills
                if key not in self._release_versions
            }
            if not repositories:
                return
            self._checking_release_versions = True

            def lookup_versions():
                versions = {}
                for key, repository in repositories.items():
                    try:
                        assets = plugin_page.github_release_assets(repository)
                        if tag := assets[0]["tag"]:
                            versions[key] = tag
                    except Exception:
                        # Download still performs a fresh lookup and reports its own errors.
                        continue
                return versions

            job = plugin_page._Job(lookup_versions)
            job.signals.finished.connect(self._show_release_versions)
            QThreadPool.globalInstance().start(job)

        @Slot(object, object, object)
        def _show_release_versions(self, _job, versions, _error):
            self._checking_release_versions = False
            if not versions:
                return
            self._release_versions.update(versions)
            for key, version in versions.items():
                if (pill := self._version_pills.get(key)) is not None:
                    pill.setText(version)
                    pill.show()

        def _download_repository(self, repository):
            if self._busy or not self._confirm_import():
                return
            self._start_job(
                lambda: plugin_page.github_release_assets(repository), self._select_release
            )

        def _install_release(self, prepared, asset, namespace=None):
            if namespace is None and asset.get("repository", "").lower() in self._repositories:
                file_name = prepared.manifest["file_name"]
                if file_name in self._listed_plugin_ids and file_name not in self._pending:
                    namespace = file_name
            super()._install_release(prepared, asset, namespace)

    plugin_page.read_plugin_source = collect_plugin_source
    plugin_page.PluginTab = PluginTab
    _PATCH_INSTALLED = True
