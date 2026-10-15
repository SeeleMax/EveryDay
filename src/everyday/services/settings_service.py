"""应用设置服务（文档 4.1 AppSettings、第六章设置页）。

**配置的真相来源只有一个：数据库的 ``app_settings`` 表。**

仓库里的 ``config/appsettings.example.json`` 有两个用途：

1. 首次运行时把模板里的非默认值灌入数据库（「首启播种」）
2. 把当前生效设置导出成 JSON，供用户查看/备份（设置页「导出配置」按钮）

这样做避免了「文件与数据库两份设置互相覆盖」这类经典问题。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from everyday import paths
from everyday.db.repositories import SettingsRepository
from everyday.models.settings import AppSettings

logger = logging.getLogger(__name__)

#: 模板中以下划线开头的键是给人看的说明文字，不参与解析
_COMMENT_PREFIX = "_"


class SettingsService:
    """设置的读写、首启播种与导出。"""

    def __init__(
        self,
        repository: SettingsRepository,
        template_path: Path | None = None,
    ) -> None:
        self._repo = repository
        self._template_path = template_path or paths.config_template_path()

    # ------------------------------------------------------------------
    def load(self) -> AppSettings:
        """读取当前设置。"""
        return self._repo.load()

    def save(self, settings: AppSettings) -> AppSettings:
        """整份保存设置。"""
        self._repo.save(settings)
        return settings

    def update(self, **changes: Any) -> AppSettings:
        """读-改-写：只更新指定字段。"""
        current = self._repo.load()
        updated = current.merged(**changes)
        self._repo.save(updated)
        logger.debug("设置已更新：%s", ", ".join(changes))
        return updated

    # ------------------------------------------------------------------
    def is_first_run(self) -> bool:
        """数据库里是否还没有任何设置项。"""
        return not self._repo.get_raw("auto_start", None) and not self._repo.get_raw(
            "float_window_visible", None
        )

    def seed_from_template(self) -> bool:
        """首次运行时用模板为数据库播种。

        :return: 是否真的写入了数据（非首启或模板缺失时返回 ``False``）
        """
        if not self.is_first_run():
            return False

        values: dict[str, Any] = {}
        if self._template_path.is_file():
            try:
                raw = json.loads(self._template_path.read_text(encoding="utf-8"))
                values = {
                    key: value
                    for key, value in raw.items()
                    if not key.startswith(_COMMENT_PREFIX)
                }
                logger.info("从模板播种设置：%s", self._template_path)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("配置模板无法读取，改用内置默认值：%s", exc)
        else:
            logger.info("未找到配置模板，使用内置默认值：%s", self._template_path)

        settings = AppSettings.from_kv(values) if values else AppSettings()
        self._repo.save(settings)
        return True

    def export_to_json(self, target: Path | None = None) -> Path:
        """把当前生效设置导出为 JSON 文件。"""
        destination = target or paths.data_dir() / "appsettings.exported.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = self._repo.load().to_kv()
        # 数值/布尔以原生类型写出，便于人阅读
        readable: dict[str, Any] = {}
        for key, value in payload.items():
            if value in ("true", "false"):
                readable[key] = value == "true"
            else:
                try:
                    readable[key] = float(value) if "." in value else int(value)
                except ValueError:
                    readable[key] = value
        destination.write_text(
            json.dumps(readable, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("设置已导出到 %s", destination)
        return destination
