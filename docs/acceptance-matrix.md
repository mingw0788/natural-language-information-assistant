# 需求验收矩阵

本矩阵对应工作区根目录的《自然语言信息采集助手-需求分析.md》。状态按当前实现与自动化测试记录；“部分覆盖”表示有相关组件测试，但完整用户场景或要求中的边界仍未验证。P1 功能列为延期，不伪装成通过。

状态说明：**已验证**＝关联测试通过且覆盖了所列行为；**部分覆盖**＝仅验证部分规则、接口或组件；**延期**＝属于首版范围外。

按用户后续要求，当前默认模式调整为普通网页搜索（`browser`，必应默认、百度可选）和本地读取（`none`），不需要 API、密钥或云模型。搜索词仍联网发送到所选搜索网站。普通搜索页采用用户发起读取模式，不保证其搜索路径的自动访问许可；来源网页继续检查 robots 规则。验证码或访问限制会停止搜索并说明原因，不承诺所有网站可读。

## 功能需求

| 编号 | 状态 | 测试或依据 |
|---|---|---|
| FR-001 | 部分覆盖 | `test_interpret_identifies_facts_lists_and_comparisons`、`test_search_query_keeps_subject_and_requested_details_without_request_filler`、`test_task_preview_returns_focused_search_terms_for_natural_language`；自然语言解析和搜索词整理有单元与 API 覆盖，尚无完整真实浏览器建任务验收。 |
| FR-002 | 部分覆盖 | `test_interpret_does_not_infer_region_or_time_and_separates_conditions`、`test_home_page_is_local_app`、`test_task_preview_returns_focused_search_terms_for_natural_language`；页面会展示搜索词与服务缺失状态，真实浏览器交互仅部分验证。 |
| FR-003 | 部分覆盖 | `test_same_name_without_identity_context_is_ambiguous`、`test_entity_resolution_merges_same_host_alias_and_separates_other_hosts`；支持保守区分同名实体；开放探索的代表性候选未完整验收。 |
| FR-004 | 已验证 | `test_task_spec_keeps_required_and_preferred_conditions_separate`、`test_interpret_does_not_infer_region_or_time_and_separates_conditions`。 |
| FR-005 | 部分覆盖 | `test_interpret_identifies_facts_lists_and_comparisons`、`test_optional_field_missing_keeps_entity_but_required_unknown_is_pending`；任务类型和部分停止判断有覆盖，三种交付的端到端质量尚未验收。 |
| FR-006 | 部分覆盖 | `test_multiple_urls_and_supplemental_text_are_preserved`、`test_missing_search_configuration_is_reported_before_run`；多网址与材料归属有覆盖，严格唯一来源失败时不扩展来源的专门验收不足。 |
| FR-007 | 部分覆盖 | `test_browser_search_queries_public_bing_page_and_extracts_organic_results`、`test_browser_search_parses_real_public_bing_fixture`、`test_browser_search_reports_challenge_pages_without_retrying`、`test_search_snippets_are_only_leads_and_failed_source_is_partial`；默认读取普通搜索页，真实公开页面保存样本覆盖结果解析，摘要仍只作为线索。验证码停止、补网址入口有覆盖；持续真实联网兼容性不作保证。可选 Brave/百度 API 另有适配器测试。 |
| FR-008 | 部分覆盖 | `test_supplemental_search_is_bounded_and_stops_at_requested_count`、`test_search_and_supplement_rounds_have_a_hard_call_cap`；轮次和数量有硬限制，针对每个具体缺口的补搜策略未覆盖完整。 |
| FR-009 | 部分覆盖 | `test_fetcher_reads_fixture_and_honors_robots_and_private_redirects`、`test_pdf_reader_reports_scan_without_ocr_as_unsupported`、`test_browser_renders_dynamic_text_and_blocks_private_subrequest`。 |
| FR-010 | 部分覆盖 | `CollectionRunner` 对单来源最多读取 5 页；尚无重复分页内容和循环链接的专项集成用例。 |
| FR-011 | 已验证 | `test_search_snippets_are_only_leads_and_failed_source_is_partial`、`test_fetcher_reads_fixture_and_honors_robots_and_private_redirects`。 |
| FR-012 | 部分覆盖 | `test_model_observation_is_saved_only_with_exact_locatable_evidence`、`test_local_fallback_extracts_explicit_field_label_with_exact_span`；本地模式限于规则和明确字段格式，不具备完整语言模型能力。复杂理解、表格脚注等仍依赖可选模型且未全面验收。 |
| FR-013 | 已验证 | `test_evidence_ref_requires_quote_matching_span_length`、`test_evidence_validator_requires_exact_snapshot_substring`、`test_runner_persists_exact_evidence_and_completes`。 |
| FR-014 | 已验证 | `test_missing_value_is_not_coerced_to_zero`、`test_different_currency_or_period_is_not_ranked_as_comparable`。 |
| FR-015 | 已验证 | `test_optional_field_missing_keeps_entity_but_required_unknown_is_pending`、`test_pdf_reader_reports_scan_without_ocr_as_unsupported`。 |
| FR-016 | 部分覆盖 | `test_same_title_at_different_companies_is_not_merged`、`test_same_name_without_identity_context_is_ambiguous`、`test_entity_resolution_merges_same_host_alias_and_separates_other_hosts`；合并依据目前限于名称和来源主机，人工撤销合并尚未实现。 |
| FR-017 | 已验证 | `test_conflict_keeps_both_values_and_collapses_syndicated_quote_support`。 |
| FR-018 | 部分覆盖 | `test_optional_field_missing_keeps_entity_but_required_unknown_is_pending`、`test_required_free_condition_uses_evidence_and_explicit_negation`；有筛选理由，排序理由及完整推荐解释未覆盖。 |
| FR-019 | 部分覆盖 | `test_runner_persists_exact_evidence_and_completes`、`test_pause_stops_before_dispatching_next_source`、`test_no_api_collection_exposes_source_text_without_model_extraction`、`test_source_excerpts_show_without_entities_and_cannot_inject_html`；`results.sources` 返回真实来源原文，未生成结构化实体时仍展示。长任务完整增量阅读体验未全面验收。 |
| FR-020 | 部分覆盖 | `test_local_collection_results_correction_and_undo`、`test_result_selection_survives_refresh_and_comparison_is_side_by_side`；视图 API、排序、选择保持及并排比较有覆盖，四类计数与大结果集体验未完整验收。 |
| FR-021 | 部分覆盖 | `test_message_view_filter_does_not_change_collection_spec`、`test_adding_display_field_creates_updated_spec_without_reinterpreting_scope`；常见展示调整有覆盖，其余自然语言操作短语仍需扩充。 |
| FR-022 | 部分覆盖 | `test_local_collection_results_correction_and_undo`；字段修正和撤销有 API 覆盖，修正后必要条件重新评估及迟到来源冲突的完整场景未覆盖。 |
| FR-023 | 部分覆盖 | `test_create_task_and_version_keeps_prior_spec_immutable`、`test_recent_time_window_is_frozen_at_each_run_start`；版本和相对时间有覆盖，旧版本慢请求隔离仍需集成验收。 |
| FR-024 | 部分覆盖 | `test_only_one_run_can_be_active_and_pause_resume_persist`、`test_run_control_routes_pause_resume_and_cancel_without_resetting_usage`、`test_pause_stops_before_dispatching_next_source`、`test_recovery_preserves_progress_and_does_not_redispatch_uncertain_source`；恢复、暂停、停止和不重派不确定请求有覆盖，跨日真实交互未端到端验证。 |
| FR-025 | 已验证 | `test_reservation_counts_in_flight_work_against_run_and_daily_limits`、`test_unknown_call_is_charged_at_reserved_maximum_and_release_is_explicit`、`test_concurrent_reservations_cannot_exceed_last_daily_unit`。 |
| FR-026 | 部分覆盖 | `test_reservation_counts_in_flight_work_against_run_and_daily_limits`、`test_model_provider_requests_schema_json_and_parses_usage`；显示调用与 Token 用量及无法保证精确费用封顶的说明，供应商价格估算未实现。 |
| FR-027 | 部分覆盖 | `test_csv_escapes_formula_text_and_includes_trace_metadata`、`test_xlsx_uses_text_cells_and_formula_safety`、`test_json_and_markdown_are_auditable_and_export_is_recorded`；格式和安全有服务测试，浏览器下载及完整筛选字段组合未全面验收。 |
| FR-028 | 延期 | P1：收藏、短名单和个人备注未实现。 |
| FR-029 | 延期 | P1：长期偏好、模板及记忆管理未实现。 |
| FR-030 | 部分覆盖 | `test_old_snapshots_are_removed_but_excerpt_is_retained_unverified`、`test_expired_derived_results_are_removed_after_180_days`、`test_explicit_task_material_deletion_can_keep_derived_quote`；保留和删除规则有服务测试，历史检索分页与手动保留入口未完整验收。 |
| FR-031 | 部分覆盖 | `test_discovery_defaults_to_no_api_web_search`、`test_default_search_and_local_reading_save_without_any_keys`、`test_no_api_collection_exposes_source_text_without_model_extraction`、`test_source_excerpts_show_without_entities_and_cannot_inject_html`；默认无密钥即可搜索并显示实际来源，不调用云模型。选择需密钥的可选 API 时，仍由 `test_missing_search_configuration_is_reported_before_run` 检查缺失配置；公开网站持续可用性与复杂提取质量未全面验收。 |
| FR-032 | 已验证 | `test_provider_settings_never_return_secret_and_require_csrf`、`test_foreign_host_and_origin_are_rejected`、`test_url_policy_rejects_non_web_and_non_public_targets`、`test_dns_result_is_pinned_and_private_answer_is_rejected`、`test_browser_renders_dynamic_text_and_blocks_private_subrequest`（本机 Edge/Chromium）。 |
| FR-033 | 部分覆盖 | `test_runner_persists_exact_evidence_and_completes`、`test_recovery_preserves_progress_and_does_not_redispatch_uncertain_source`；状态事件有覆盖，通知节制和用户可理解性未做正式可用性验收。 |
| FR-034 | 延期 | P1 自动更新和自动差异报告延期；按用户要求新建一轮查询可用 `start_run` API，复用 FR-023 测试。 |
| FR-035 | 延期 | P1：本地 PDF 文件上传未实现；首版只接受公开 PDF 网址。 |
| FR-036 | 延期 | P1：计划监控与通知未实现。 |

## 非功能需求

| 编号 | 状态 | 测试或依据 |
|---|---|---|
| NFR-01 | 部分覆盖 | `test_home_page_is_local_app`、`test_settings_and_history_pages_render_for_narrow_viewports`；无正式可用性研究。 |
| NFR-02 | 部分覆盖 | `test_healthz_returns_ready` 和 API TestClient；尚无本地动作 1 秒反馈的性能计时测试。 |
| NFR-03 | 部分覆盖 | `test_runner_persists_exact_evidence_and_completes`、`test_result_selection_survives_refresh_and_comparison_is_side_by_side`；持久化和界面刷新有覆盖，持续长任务的阅读位置保持未覆盖。 |
| NFR-04 | 已验证 | `test_snapshot_and_observation_write_are_idempotent`、`test_recovery_preserves_progress_and_does_not_redispatch_uncertain_source`、`test_pause_and_restart_do_not_reset_the_active_time_budget`。 |
| NFR-05 | 部分覆盖 | `test_optional_field_missing_keeps_entity_but_required_unknown_is_pending`、`test_runner_persists_exact_evidence_and_completes`；理由和事件可见，完整停止原因可读性未做用户验收。 |
| NFR-06 | 已验证 | 凭据脱敏、CSRF/Host/Origin、URL/DNS、浏览器子请求和公式注入相关测试见 FR-027、FR-032。 |
| NFR-07 | 部分覆盖 | `test_default_search_and_local_reading_save_without_any_keys`、`test_switching_search_does_not_reuse_another_services_key`、`test_provider_metadata_is_persisted_without_secrets`、`test_provider_settings_never_return_secret_and_require_csrf`；默认不需要凭据，可选服务的密钥隔离及脱敏有覆盖。 |
| NFR-08 | 部分覆盖 | `test_settings_and_history_pages_render_for_narrow_viewports` 与 `test_result_selection_survives_refresh_and_comparison_is_side_by_side`；语义控件、可见状态已采用，键盘和读屏专项验收未完成。 |
| NFR-09 | 部分覆盖 | provider、URL、PDF、动态网页与 `tests/fixtures/bing_search_public.html` 真实公开页保存样本覆盖代表性边界；`test_browser_search_parses_real_public_bing_fixture` 检查实际页面结构，网站将来改版适配不在样本保证范围。 |
| NFR-10 | 已验证 | `test_only_one_run_can_be_active_and_pause_resume_persist`；本地调度单个主动运行。 |

## 验收场景

| 编号 | 状态 | 测试或差距 |
|---|---|---|
| AC-01 | 部分覆盖 | `test_interpret_identifies_facts_lists_and_comparisons`；自然语言解释有覆盖，完整界面连续操作未端到端验证。 |
| AC-02 | 已验证 | `test_interpret_does_not_infer_region_or_time_and_separates_conditions`。 |
| AC-03 | 部分覆盖 | `test_recent_resolves_to_visible_seven_day_window`、`test_recent_time_window_is_frozen_at_each_run_start`；无发布日期来源的筛选边界未单测。 |
| AC-04 | 部分覆盖 | `test_interpret_identifies_facts_lists_and_comparisons`、`test_result_selection_survives_refresh_and_comparison_is_side_by_side`；事实、名单、比较均未构成端到端样例组。 |
| AC-05 | 部分覆盖 | `test_same_name_without_identity_context_is_ambiguous`、`test_entity_resolution_merges_same_host_alias_and_separates_other_hosts`；开放主题候选生成未验收。 |
| AC-06 | 部分覆盖 | `test_multiple_urls_and_supplemental_text_are_preserved`；混合粘贴材料与运行中补充来源未完整验收。 |
| AC-07 | 部分覆盖 | `test_missing_search_configuration_is_reported_before_run`；严格单一来源失败时的 UI 场景未单测。 |
| AC-08 | 已验证 | `test_brave_search_maps_results_without_treating_snippet_as_evidence`、`test_browser_search_parses_real_public_bing_fixture`、`test_search_snippets_are_only_leads_and_failed_source_is_partial`；搜索摘要仍不能替代实际来源正文。 |
| AC-09 | 部分覆盖 | `test_supplemental_search_is_bounded_and_stops_at_requested_count`、`test_search_and_supplement_rounds_have_a_hard_call_cap`；无新增结果时的缺口说明未端到端验收。 |
| AC-10 | 部分覆盖 | `test_pdf_reader_reports_scan_without_ocr_as_unsupported`、`test_browser_renders_dynamic_text_and_blocks_private_subrequest`、`test_fetcher_reads_fixture_and_honors_robots_and_private_redirects`；混合来源单轮场景未覆盖。 |
| AC-11 | 部分覆盖 | 单来源最多 5 页的实现；尚无重复分页循环 fixture。 |
| AC-12 | 部分覆盖 | `test_model_observation_is_saved_only_with_exact_locatable_evidence`；各限定词与脚注的组合未逐项覆盖。 |
| AC-13 | 已验证 | `test_optional_field_missing_keeps_entity_but_required_unknown_is_pending`。 |
| AC-14 | 部分覆盖 | `test_same_title_at_different_companies_is_not_merged`、`test_conflict_keeps_both_values_and_collapses_syndicated_quote_support`；规格、地点与可撤销归并仍需扩充。 |
| AC-15 | 已验证 | `test_different_currency_or_period_is_not_ranked_as_comparable`、`test_missing_value_is_not_coerced_to_zero`。 |
| AC-16 | 已验证 | `test_conflict_keeps_both_values_and_collapses_syndicated_quote_support`。 |
| AC-17 | 部分覆盖 | `test_result_selection_survives_refresh_and_comparison_is_side_by_side`；异步新增时的滚动位置和阅读体验未验收。 |
| AC-18 | 部分覆盖 | `test_message_view_filter_does_not_change_collection_spec`；筛选后续继续搜索的完整 UI 流程未覆盖。 |
| AC-19 | 部分覆盖 | `test_supplemental_search_is_bounded_and_stops_at_requested_count`、`test_adding_display_field_creates_updated_spec_without_reinterpreting_scope`；撤销任务条件尚未实现。 |
| AC-20 | 部分覆盖 | `test_create_task_and_version_keeps_prior_spec_immutable`、`test_recent_time_window_is_frozen_at_each_run_start`；迟到旧版本结果隔离未完成专项测试。 |
| AC-21 | 部分覆盖 | `test_local_collection_results_correction_and_undo`；修正触发必要条件重评估及之后来源冲突仍并列的完整场景不足。 |
| AC-22 | 部分覆盖 | `test_pause_and_restart_do_not_reset_the_active_time_budget`、`test_run_control_routes_pause_resume_and_cancel_without_resetting_usage`、`test_recovery_preserves_progress_and_does_not_redispatch_uncertain_source`；跨日状态和变化过期判定未完整验收。 |
| AC-23 | 已验证 | `test_reservation_counts_in_flight_work_against_run_and_daily_limits`、`test_unknown_call_is_charged_at_reserved_maximum_and_release_is_explicit`、`test_concurrent_reservations_cannot_exceed_last_daily_unit`。 |
| AC-24 | 部分覆盖 | `test_model_provider_requests_schema_json_and_parses_usage`；价格未知时的页面措辞和供应商账单核对未覆盖。 |
| AC-25 | 部分覆盖 | `test_csv_escapes_formula_text_and_includes_trace_metadata`、`test_xlsx_uses_text_cells_and_formula_safety`、`test_json_and_markdown_are_auditable_and_export_is_recorded`；真实下载文件打开验收不足。 |
| AC-26 | 部分覆盖 | `test_old_snapshots_are_removed_but_excerpt_is_retained_unverified`、`test_expired_derived_results_are_removed_after_180_days`、`test_explicit_task_material_deletion_can_keep_derived_quote`；共享快照引用和删除后的跨结果重判需进一步集成测试。 |
| AC-27 | 部分覆盖 | `test_discovery_defaults_to_no_api_web_search`、`test_default_search_and_local_reading_save_without_any_keys`、`test_missing_search_configuration_is_reported_before_run`、`test_browser_search_verification_reason_is_preserved`；默认免密钥可启动，可选 API 缺失配置提前提示，验证码停止原因保留；任意网站的完整用户流程未全面验收。 |
| AC-28 | 已验证 | `test_url_policy_rejects_non_web_and_non_public_targets`、`test_dns_result_is_pinned_and_private_answer_is_rejected`、`test_browser_renders_dynamic_text_and_blocks_private_subrequest`、`test_provider_settings_never_return_secret_and_require_csrf`。 |
| AC-29 | 部分覆盖 | `test_runner_persists_exact_evidence_and_completes`、`test_search_snippets_are_only_leads_and_failed_source_is_partial`；无进展时通知节制未做时间驱动的 UI 测试。 |
| AC-30 | 延期 | P1 收藏和备注未实现。 |
| AC-31 | 延期 | P1 长期偏好与模板未实现。 |
| AC-32 | 延期 | P1 自动变化报告延期；手动新运行由 `test_recent_time_window_is_frozen_at_each_run_start` 部分覆盖。 |
| AC-33 | 延期 | P1 本地文件上传未实现。 |
| AC-34 | 延期 | P1 定时监控与通知未实现。 |
| AC-35 | 部分覆盖 | `test_settings_and_history_pages_render_for_narrow_viewports`、`test_result_selection_survives_refresh_and_comparison_is_side_by_side`；本地响应耗时、键盘导航和存储空间不足恢复未测。 |
| AC-36 | 部分覆盖 | `test_provider_metadata_is_persisted_without_secrets`、`test_url_policy_rejects_non_web_and_non_public_targets`；来源规则版本变化与超规模提示未完整验收。 |

## 当前已知验收缺口

- 本轮自动化主要是单元、API 与局部浏览器交互测试；尚未覆盖 AC-01–29、AC-35–36 全部完整用户旅程。
- 普通搜索页依赖网站公开页面结构，可能出现验证码、限流或改版；真实保存样本与一次联网读取不能保证未来可用。不绕过验证，可补网址继续。来源网页 robots 检查与普通搜索页用户发起读取模式分别实现。
- 默认本地模式能展示实际来源原文，复杂自然语言理解和结构化提取仍有限；原文卡片不伪装成模型答案，也不参与实体比较。
- 实体归并只使用规范名称和来源主机，遇到跨站同名保守分列；尚无用户可撤销的合并管理界面。
- 对象比较提供选中对象的字段并排、状态和证据链接；不做单位换算、数值排序或推荐排名。
- 运行费用按请求与 Token 展示用量，不对供应商账单金额作精确封顶承诺。
- P1 功能按需求延期；本地 PDF 上传仍属于 P1，首版 PDF 仅支持公开网址中的可选文字内容。
