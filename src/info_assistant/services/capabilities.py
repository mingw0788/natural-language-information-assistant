from info_assistant.domain.models import CapabilityReport, TaskSpec
from info_assistant.services.provider_settings import ProviderSettingsStore


class CapabilityChecker:
    def __init__(self, settings: ProviderSettingsStore) -> None:
        self.settings = settings

    def assess(self, spec: TaskSpec) -> CapabilityReport:
        providers = self.settings.public_settings()
        has_direct_material = bool(spec.pasted_material) or any(
            source.kind in {"url", "text"} for source in spec.sources
        )
        missing: list[str] = []
        available: list[str] = []
        fallbacks: list[str] = []
        data_flow: list[str] = []

        if providers.search_configured:
            available.append("网页搜索")
            if providers.search_provider == "browser":
                data_flow.append(
                    f"搜索词会发送到{providers.search_provider_label}的普通搜索页面，无需 API 密钥"
                )
            else:
                data_flow.append(f"搜索词会发送到已选择的{providers.search_provider_label}")
        elif not has_direct_material:
            missing.append("搜索服务：需要配置搜索密钥才能自动发现网页")
        if providers.model_configured:
            available.append("结构化模型提取")
            data_flow.append("任务文字和选中的来源正文会发送到已配置的模型服务")
        else:
            fallbacks.append("在本机显示已抓取的来源原文，并提取明确的字段；不调用云模型")
        if has_direct_material:
            available.append("直接来源读取")
            fallbacks.append("直接读取用户提供的网址和粘贴文字")
        if not missing:
            fallbacks.append("未配置的服务不会被调用；可完成部分会单独标明")
        return CapabilityReport(
            ready=not missing,
            available=available,
            missing=missing,
            data_flow=data_flow,
            local_fallbacks=fallbacks,
        )
