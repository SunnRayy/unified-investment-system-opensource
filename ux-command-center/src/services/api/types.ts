// All shared TypeScript types and interfaces — extracted verbatim from api.ts
// Pure type declarations; no runtime code.

export interface MacroIndicator {
    value: number | null;
    display_value: string;
    zone: string;
    zone_color: string;
}

export interface MarketStatusComposite {
    verdict: string;
    verdict_color: string;
    score: number;
    red_count: number;
    total_count: number;
    sections: {
        equity_macro: number | null;
        gold: number | null;
        crypto: number | null;
    };
}

export interface KPI {
    net_worth: number;
    cash_available?: number;
    pnl_24h: number | null;
    market_pulse: number | null;
    market_pulse_source?: string;
    market_pulse_sentiment: string | null;
    vix?: MacroIndicator | null;
    brent_crude?: MacroIndicator | null;
    us10y?: MacroIndicator | null;
}


export interface AuditLog {
    sync_timestamp: string;
    source_system: string;
    target_table: string;
    record_key: string;
    conflict_type: string;
    source_value: string;
    target_value: string;
    resolution: string;
}

export interface AuditSummary {
    total_logs: number;
    last_sync_timestamp: string | null;
    unresolved_conflicts: number;
}


export interface AllocationItem {
    name: string;
    value: number;
    color?: string;
}

export interface CompassSummary {
    total_net_worth: number;
    drift_index: number;
    classes_in_drift: number;
    total_classes: number;
    last_sync_date: string;
    last_sync_source: string;
}

export interface AllocationRow {
    asset_class: string;
    current_value: number;
    currency: string;
    current_pct: number;
    target_pct: number;
    drift_pct: number;
    tolerance_pct: number;
    status: "within_range" | "over" | "under" | "no_target";
    /** False when no risk-profile target exists for this class. `target_pct`
     *  and `drift_pct` are then placeholders, not measurements — render "—".
     *  "No target set" and "target is 0%" are different facts; conflating them
     *  is what made a fresh install flag every class as over target. */
    has_target: boolean;
    is_top_level: boolean;
    parent_class: string | null;
    // Provisional fields — only present when include_pending=true
    provisional_value?: number | null;
    provisional_pct?: number | null;
    provisional_delta_cny?: number | null;
}

export interface CompassAllocationMeta {
    pending_trade_count: number;
    is_provisional: true;
}

export interface CompassAllocationEnvelope {
    allocation: AllocationRow[];
    meta: CompassAllocationMeta;
}

export interface CompassMarkdown {
    top_level_table: string;
    sub_class_table: string;
    generated_at: string;
}

export interface WealthAsset {
    name: string;
    code: string;
    type: string;
    /** Chinese companion for `type` (taxonomy_classes.name_cn), Program BIL / WS-9. */
    type_cn?: string | null;
    period: string;
    status: string;
    /**
     * invested / pl / pl_native / ret are null for balance-only assets — a reported
     * balance (e.g. a Financial-Summary bond column) whose cost basis is genuinely
     * unknown (no cost, no transactions). The UI renders these as "—" rather than a
     * fabricated ¥0 gain. See src/services/currency.py::is_balance_only_holding.
     */
    invested: number | null;
    cur: number;
    pl: number | null;
    pl_native?: number | null;
    pnl_currency?: string;
    ret: number | null;
    /**
     * Current-lot unrealized return % — (current_price − avg_lot_cost) / avg_lot_cost × 100.
     * Computed from holdings.cost_price_unit (FIFO weighted avg cost per unit), which IS the
     * current-lot basis.  Same formula as scan_value_traps (Fix 1, 2026-07-10).
     * Null for closed assets, cash equivalents, or assets with no cost data.
     */
    unrealized_current_lots_pct?: number | null;
    /** True when this asset has an open value-trap review (F2.4 badge). */
    open_value_trap_review?: boolean;
    /**
     * True when this row's P&L comes from an owner-entered override rather than a
     * reader ledger (#7). Drives the "Logged" badge and edit-vs-log affordance.
     */
    has_manual_data?: boolean;
    /**
     * True when the owner may log P&L for this asset (#7) — no authoritative
     * reader ledger feeds it, so an override would be honoured rather than
     * superseded. Backend-resolved: do NOT infer it from `pl == null`, which
     * misses the assets that show a real-looking +¥0.00 (bank wealth, pension).
     */
    can_log_manual_pnl?: boolean;
}

export interface WealthOSSummary {
    total_lifetime_gain: number;
    lifetime_gain_pct: number;
    annualized_return: number | null;
    active_asset_count: number;
    total_asset_count: number;
}


export interface HistoryItem {
    name: string;
    value: number;
}

export interface RiskMetrics {
    volatility: number;
    volatility_status?: string;
    sharpe: number;
    sharpe_status?: string;
    var_95: number;
    var_95_status?: string;
    beta: number;
    div_score: number;
}

export interface CorrelationMatrixRow {
    asset: string;
    correlations: { [key: string]: CorrelationCell | null };
}

export interface CorrelationCell {
    value: number | null;
    overlap: number;
    low_confidence: boolean;
}

export interface RiskCorrelation {
    matrix: CorrelationMatrixRow[];
    assets: string[];
    method: string;
    effective_periods?: number;
    overlap_min?: number;
    overlap_median?: number;
    insufficient_pairs?: number;
    total_pairs?: number;
    window_start?: string;
    window_end?: string;
    min_overlap_periods?: number;
    winsor_p_low?: number;
    winsor_p_high?: number;
    excluded_jump_dates?: string[];
    excluded_jump_points_count?: number;
    excluded_jump_points_by_class?: Record<string, number>;
    clipped_points_by_class?: Record<string, number>;
    clipped_pair_share?: number;
}


export interface PerformanceSummaryResponse {
    net_worth: number;
    total_cost_basis: number;
    total_unrealized_pl: number;
    unrealized_pl_pct: number;
    total_realized_pl: number;
    total_lifetime_pl: number;
    asset_count: number;
    snapshot_date: string;
}

export interface GainsAsset {
    asset_id: string;
    name: string;
    top_class: string;
    top_class_cn?: string | null;
    currency: string;
    cost_basis: number;
    market_value: number;
    unrealized_pl: number;
    realized_pl: number;
    pnl_currency?: string;
    unrealized_pl_native?: number;
    realized_pl_native?: number;
    return_pct: number;
}

export interface GainsResponse {
    total_unrealized_pl: number;
    total_realized_pl: number;
    total_lifetime_pl: number;
    total_cost_basis: number;
    total_market_value: number;
    unrealized_pl_pct: number;
    assets: GainsAsset[];
}

export interface ClassPerformance {
    class_name: string;
    class_name_cn?: string | null;
    market_value: number;
    cost_basis: number;
    unrealized_pl: number;
    realized_pl: number;
    lifetime_pl: number;
    return_pct: number;
    weight_pct: number;
    asset_count: number;
}

export interface SubClassPerformance {
    top_class: string;
    sub_class: string;
    top_class_cn?: string | null;
    sub_class_cn?: string | null;
    market_value: number;
    cost_basis: number;
    unrealized_pl: number;
    realized_pl: number;
    lifetime_pl: number;
    return_pct: number;
    weight_pct: number;
    asset_count: number;
}

export interface PerformanceByClassResponse {
    total_market_value: number;
    total_cost_basis: number;
    top_classes: ClassPerformance[];
    sub_classes: SubClassPerformance[];
}

export interface VerificationPeriod {
    period_start: string;
    period_end: string;
    adoption_rate: number | null;
    portfolio_return: number | null;
    benchmark_return: number | null;
    alpha: number | null;
    max_drift: number | null;
    total_insights: number;
    verdict_hit_rate?: number | null;
    good_calls?: number | null;
    total_scored?: number | null;
    adoption_history?: AdoptionMonth[];
    verdict_breakdown?: VerdictMonth[];
}

export interface AdoptionMonth {
    period_start: string;
    adoption_rate: number;
    total: number;
    adopted: number;
}

export interface VerdictMonth {
    period_start: string;
    good_calls: number;
    regrets: number;
    missed_opportunity: number;
    bullet_dodged: number;
    total_scored: number;
}

export interface VerificationTrends {
    periods: VerificationPeriod[];
}

export interface DecisionItemMetadata {
    category?: string;
    tags?: string[];
    asset_class?: string;
    deviation_pct?: number;
    asset_id?: string;
    action?: string;
    amount?: number;
    linked_title?: string | null;
    linked_ref?: string | null;
    effective_source?: string | null;
    reason_excerpt?: string | null;
    [key: string]: unknown;
}

export interface DecisionItem {
    id: string;
    type: 'insight' | 'trade' | 'drift';
    date: string;
    title: string;
    content: string;
    source: string;
    status: string;
    subtype?: string | null;
    display_source?: string | null;
    display_status?: string | null;
    match_status?: string | null;
    verification_status?: 'pending' | 'verified' | 'unmatched' | null;
    origin_ref?: string | null;
    metadata: DecisionItemMetadata;
}

export interface DecisionTimeline {
    items: DecisionItem[];
    summary: {
        total: number;
        adopted: number;
        pending: number;
    };
}

export interface DecisionStats {
    total_insights: number;
    adopted_count: number;
    pending_count: number;
    pending_actions_count?: number;
    adoption_rate: number;
    total_trades: number;
    /** Count of everything the timeline renders — insights, drift alerts and
     *  display-scope trades. Use this for the "Total Decisions" tile: adding
     *  `total_insights + ai_trades_total` counts only AI-attributed trades and
     *  produced a zero sitting directly above a populated timeline. */
    total_decisions?: number;
    timeline_counts?: { insight: number; drift: number; trade: number; total: number };
    active_drift_alerts: number;
    ai_trades_total?: number;
    by_model?: Record<string, { total: number, adopted: number }>;
}


export interface ScorecardItem {
    id: number;
    date: string;
    asset_id: string;
    asset_name: string | null;
    action: string;
    source: string | null;
    verification_status?: 'pending' | 'verified' | 'unmatched' | null;
    verification_result: string | null;
    verdict: 'good_call' | 'regret' | 'missed_opportunity' | 'bullet_dodged' | null;
    outcome_pct: number | null;
    grade: 'A' | 'B' | null;
    linked_insight_id?: number | null;
    linked_insight_title?: string | null;
    match_status?: 'matched' | 'inferred' | 'unmatched' | 'source_only' | null;
    why_unscored?: string | null;
}

export interface DecisionScorecard {
    items: ScorecardItem[];
}

export interface DecisionFunnel {
    total: number;
    adopted: number;
    rejected: number;
    pending: number;
    good_call: number;
    regret: number;
    missed_opportunity: number;
    bullet_dodged: number;
    linked_adopted_trades?: number;
}

export interface LeaderboardSource {
    source: string;
    total: number;
    scored: number;
    good_call: number;
    hit_rate: number;
    avg_outcome_pct: number | null;
}

export interface DecisionLeaderboard {
    sources: LeaderboardSource[];
}

export interface DecisionPatternSource {
    source: string;
    total: number;
    adopted: number;
    rejected: number;
    pending: number;
    linked_trades?: number;
}

export interface IntelligenceGrowthItem {
    id: string;
    date: string;
    title: string;
    content: string;
    source: string;
    origin_ref: string;
}

export interface IntelligenceRawSection {
    section: string;
    title: string;
    content: string;
    entry_count: number;
}

export interface DecisionIntelligence {
    decision_patterns: {
        funnel: DecisionFunnel;
        leaderboard: LeaderboardSource[];
        sources: DecisionPatternSource[];
    };
    growth_timeline: IntelligenceGrowthItem[];
    raw_sections: IntelligenceRawSection[];
}

export interface StrategyScopeAlignmentItem {
    actual_pct: number;
    target_pct: number | null;
    drift_pct: number | null;
    status: 'aligned' | 'drifting';
}

export interface StrategyScopeSummary {
    included_classes: string[];
    excluded_classes?: string[];
    coverage_note: string;
}

export interface BehavioralSummaryItem {
    dimension: string;
    score: number | null;
    raw_value: number | null;
    window_days: number | null;
    label: string;
    description?: string;
    computed_at?: string | null;
}

export interface StrategyReport {
    review_date: string;
    target_scope_alignment: Record<string, StrategyScopeAlignmentItem>;
    uis_scope_alignment: Record<string, StrategyScopeAlignmentItem>;
    target_scope_summary: StrategyScopeSummary;
    uis_scope_summary: StrategyScopeSummary;
    target_scope_alignment_status: 'aligned' | 'drifting' | 'misaligned';
    uis_scope_alignment_status: 'aligned' | 'drifting' | 'misaligned';
    trading_frequency: {
        period_30d: number;
        period_60d: number;
        period_90d: number;
        monthly_rate: number;
        assessment: 'aligned' | 'moderate' | 'high_frequency';
        philosophy_threshold: number;
    };
    contrarian_score: number | null;
    contrarian_details: {
        status?: 'ok' | 'insufficient_market_context';
        sell_count: number;
        panic_sell_count: number;
        details: Array<{date: string; asset_id: string; market_return_pct: number | null; was_panic_sell: boolean}>;
    };
    profile_discrepancies: {
        uis_only: string[];
        both: string[];
    };
    behavioral_summary?: Record<string, BehavioralSummaryItem>;
}

export interface StrategyMemo {
    id: number;
    date: string;
    title: string;
    bias: 'defensive' | 'offensive' | 'neutral';
    directives: string[];
    source_file?: string | null;
    content?: string | null;
}

export interface DecisionAlert {
    category: 'drift' | 'strategy' | 'verification' | 'trading';
    priority: 'high' | 'medium' | 'low';
    title: string;
    message: string;
    data: Record<string, unknown>;
}
export interface AssetSearchResult {
    asset_id: string;
    display_name: string;
    asset_class: string | null;
    base_currency: string | null;
}

export interface TradeLogEntry {
    id: number;
    log_date: string;
    asset_id: string;
    asset_name: string | null;
    action: 'Buy' | 'Sell';
    price: number | null;
    quantity: number | null;
    amount: number | null;
    currency: string;
    decision_reason: string | null;
    suggestion_source: string;
    linked_memo_id: number | null;
    verification_status: 'pending' | 'verified' | 'unmatched' | null;
}

export interface CreateTradeRequest {
    log_date: string;
    asset_id: string;
    asset_name?: string;
    action: 'Buy' | 'Sell';
    price?: number;
    quantity?: number;
    amount?: number;
    currency?: string;
    decision_reason?: string;
    memo_id?: number;
}

export interface ActionItem {
    type: 'drift_alert' | 'rsu_vest' | 'pending_decision' | 'verification_due';
    priority: 'high' | 'medium' | 'low';
    title: string;
    subtitle?: string;
    action_url: string;
}

export interface AssetAuditResponse {
    assets: Array<{
        asset_id: string;
        asset_name: string;
        asset_class: string | null;
        source_system: string;
        class_name: string | null;
        class_name_cn: string | null;
        parent_class_name: string | null;
        parent_class_name_cn: string | null;
        market_value_cny: number | null;
        market_price: number | null;
        price_currency: string | null;
        price_source: string | null;
        quantity: number | null;
        snapshot_date: string | null;
    }>;
    total: number;
}

// --- Taxonomy Interfaces ---

export interface TaxonomyClass {
    id: number;
    name: string;
    name_cn?: string;
    parent_id?: number | null;
    level: number;
    sort_order: number;
    is_rebalanceable: boolean;
    description?: string;
    children?: TaxonomyClass[];
}

export interface TaxonomyRule {
    id: number;
    rule_type: string;
    pattern: string;
    class_id: number;
    class_name?: string;
    class_name_cn?: string | null;
    tier_id?: string | null;
    tier_name?: string | null;
    priority: number;
    source: string;
}

// --- Risk Profile Interfaces ---

export interface RiskProfile {
    id: number;
    name: string;
    name_en?: string;
    is_active: boolean;
    description?: string;
}

export interface RiskAllocation {
    class_id: number;
    class_name: string;
    class_name_cn?: string | null;
    target_pct: number;
}

// --- Management Interfaces ---

export interface Transaction {
    id: number;
    transaction_date: string;
    asset_id: string;
    asset_name?: string;
    transaction_type: string;
    quantity: number | null;
    price_unit: number | null;
    amount_net: number | null;
    commission_fee: number | null;
    currency: string;
    account: string | null;
    memo: string | null;
    source_system: string;
    verified: boolean;
}

export interface TransactionFilters {
    sources: string[];
    raw_types: string[];
    normalized_types: string[];
    accounts: string[];
}

// === Balance Sheet ===
export interface BalanceSheetSummary {
    latest_snapshot: string | null;
    snapshot_count: number;
    total_rows: number;
    rows: Array<Record<string, any>>;
    error?: string;
}

export interface BalanceSheetHistory {
    snapshots: Array<{ snapshot_date: string; items: Array<Record<string, any>> }>;
    error?: string;
}

// === Income / Expense ===
export interface IncomeExpenseSummary {
    latest_month: string | null;
    month_count: number;
    total_rows: number;
    rows: Array<Record<string, any>>;
    error?: string;
}

export interface IncomeExpenseHistory {
    months: Array<{ month: string; items: Array<Record<string, any>> }>;
    error?: string;
}

// === Monte Carlo Projection ===
export interface ProjectionDefaults {
    suggested_return: number | null;
    suggested_volatility: number | null;
    avg_monthly_investment_12m: number;
    avg_monthly_investment_36m: number;
    /** Same run-rate North Star's glide path uses: (net_external_ttm + rsu_retained_ttm) / 12.
     * null when the glide-path run-rate is not "available" (see
     * src/services/north_star_glide.py::_contribution_run_rate). */
    suggested_contribution_run_rate: number | null;
}

export interface ProjectionResult {
    years: number[];
    initial_value?: number;
    percentiles: { p10: number[]; p25: number[]; p50: number[]; p75: number[]; p90: number[] };
    final_value_stats: { mean: number; median: number; std: number; min: number; max: number };
    assumptions: { annual_return: number; annual_volatility: number; annual_contribution: number; num_simulations: number };
    goal_probability?: number;
    goal_target?: number;
}

// === Cash Flow ===
export interface CashFlowAnalysis {
    monthly: Array<{ month: string; total_income: number; total_expense: number; net: number }>;
    trends: {
        avg_income: number; avg_expense: number; avg_net: number;
        savings_rate: number; months_analyzed: number;
        latest_month?: string; latest_income?: number; latest_expense?: number;
    };
}

export interface CashFlowForecast {
    income_forecast: number[];
    expense_forecast: number[];
    net_forecast: number[];
    months: number;
    historical_months?: number;
    methods?: { income: string; expense: string };
    error?: string;
}

// === Goals ===
// `current_amount` / `monthly_contribution` (top-level) are LEGACY — static
// columns written once at goal creation, kept for backward compatibility
// only. NOT authoritative. Use `live` for display/PROGRESS/probability —
// the single source of truth also used by the "Your Path" tab (fixes the
// owner-reported "Goals card disagrees with Your Path" defect, 2026-07-26).
export interface GoalLive {
    current_amount: number;
    monthly_contribution: number | null; // null when run-rate unavailable — never fabricated as 0
    run_rate_status: string;
}

export interface Goal {
    id: number;
    name: string;
    target_amount: number;
    target_date: string;
    current_amount: number;
    monthly_contribution: number;
    goal_type: string;
    status: string;
    notes: string | null;
    created_at: string | null;
    months_remaining: number;
    live: GoalLive;
}

export interface GoalCreate {
    name: string;
    target_amount: number;
    target_date: string;
    current_amount?: number;
    monthly_contribution?: number;
    goal_type?: string;
    notes?: string;
}

// PUT /analytics/goals/{id} — deliberately excludes current_amount /
// monthly_contribution (live-derived, not editable; see Goal docstring
// in src/financial_analysis/goals.py).
export interface GoalUpdate {
    name?: string;
    target_amount?: number;
    target_date?: string;
    goal_type?: string;
    status?: string;
    notes?: string;
}

// === Performance Returns ===
export interface PerformanceReturns {
    twr_cumulative: number | null;
    twr_ytd: number | null;
    twr_1y: number | null;
    mwr_xirr: number | null;
    error?: string;
}

export type PerformancePeriod = 'all_time' | 'last_36m' | 'last_12m';

// === Attribution ===
export interface AttributionResult {
    portfolio_return: number;
    benchmark_return: number;
    excess_return: number;
    total_allocation_effect: number;
    total_selection_effect: number;
    total_interaction_effect: number;
    classes: Array<{
        class: string;
        portfolio_weight: number; benchmark_weight: number;
        portfolio_return: number; benchmark_return: number;
        allocation_effect: number; selection_effect: number;
        interaction_effect: number; total_effect: number;
    }>;
}

// === Risk Metrics (Performance) ===
export interface PerformanceRiskMetrics {
    max_drawdown: number | null;
    sharpe_ratio: number | null;
    sortino_ratio: number | null;
    calmar_ratio: number | null;
    volatility_annual: number | null;
    total_return: number | null;
    data_points: number;
    error?: string;
}

// === Market Regime ===
export interface MarketRegime {
    trend: string;
    volatility_level: string;
    volatility_30d: number | null;
    drawdown_pct: number | null;
    ma50: number | null;
    ma200: number | null;
    ma_signal: string;
    momentum_3m_pct?: number;
    score: number;
    data_points: number;
    benchmark_symbol: string;
    error?: string;
}

export interface SyncChangelogEvent {
    kind: 'warning' | 'case' | 'info';
    title: string;
    detail: string;
    date: string;
    ts: string;
}

export interface IntegrityGroupedItem {
    cat: string;
    pass: number;
    total: number;
    fails: Array<{ name: string; actual: string; thr: string; details: string }>;
}

export interface SourceReconciliationRow {
    source: string;
    db_count: number;
    db_value: number;
    prior_count: number | null;
    prior_value: number | null;
    count_delta: number | null;
    value_delta_pct: number | null;
    status: 'ok' | 'warning' | 'missing';
    last_sync: string;
}

export interface PortfolioAuditSummary {
    last_sync_timestamp: string | null;
    integrity: { passed: number; total: number; all_passed: boolean };
    integrity_grouped: IntegrityGroupedItem[];
    open_anomalies: number;
    reader_warnings: number;
    legacy_influence_cases: number;
    global_health: Array<{ key: string; label: string; status: 'ok' | 'warning' | 'review' }>;
    asset_classes: Array<{
        class_name: string;
        class_name_cn?: string | null;
        current_value: number;
        status: 'healthy' | 'warning' | 'review';
        source_signal_summary: Array<{ source_system: string; asset_count: number }>;
        open_case_count: number;
    }>;
    source_strip: Array<{ source_system: string; flagged_asset_count: number }>;
    sync_changelog: SyncChangelogEvent[];
    source_reconciliation: SourceReconciliationRow[];
}

export interface SourceGroupAsset {
    asset_id: string;
    display_name?: string;
    status: string;
    last_activity?: string | null;
    primary_signal: string;
    market_value?: number;
    legacy_influence?: boolean;
    value_issue?: boolean;
    open_case_url?: string;
    open_tx_url?: string;
    open_run_url?: string;
}

export interface SourceGroupSummary {
    group_type: 'reader_sources' | 'legacy_influence' | 'derived_secondary';
    source_system: string;
    status: string;
    asset_count: number;
    flagged_asset_count: number;
    latest_activity: string | null;
    assets: SourceGroupAsset[];
}

export interface AssetClassInvestigation {
    class_name: string;
    class_name_cn?: string | null;
    total_value: number;
    active_assets: number;
    open_cases: number;
    groups: SourceGroupSummary[];
}

export interface SourceTraceEvent {
    timestamp: string | null;
    evidence_type: string;
    source_system: string;
    description: string;
}

export interface AssetCaseFile {
    asset_id: string;
    display_name: string;
    breadcrumb: { portfolio: string; asset_class: string; asset: string };
    severity: string;
    current_state: {
        active_source: string;
        active_shadow_status: boolean;
        current_quantity: number;
        current_market_value: number;
        last_snapshot_date: string | null;
    };
    authority_context: {
        expected_authority_source: string;
        competing_sources: string[];
        legacy_influence_flag: boolean;
        shadow_conflict_flag: boolean;
    };
    signals: string[];
    source_trace: SourceTraceEvent[];
    evidence_counts: { transactions: number; snapshots: number; sync_runs: number };
    quick_actions: { transactions: string; sync_history: string };
}

export interface SyncHistoryRun {
    id: string;
    timestamp: string | null;
    type: string;
    net_worth_delta: number;
    integrity_result: string;
    /** "ok" | "degraded" (advisory-only failures) | "failed" (≥1 blocking failure) */
    integrity_status: 'ok' | 'degraded' | 'failed';
    /** Number of blocking checks that failed (0 when status is ok or degraded). */
    blocking_failed: number;
    warning_count: number;
    sources_affected: string[];
    alert: boolean;
    is_no_change: boolean;
}

export interface SyncHistoryDetail {
    id: string;
    timestamp: string | null;
    type: string;
    net_worth_before: number;
    net_worth_after: number;
    net_worth_delta: number;
    integrity_result: string;
    /** "ok" | "degraded" (advisory-only failures) | "failed" (≥1 blocking failure) */
    integrity_status: 'ok' | 'degraded' | 'failed';
    /** Number of blocking checks that failed (0 when status is ok or degraded). */
    blocking_failed: number;
    by_source_before: Record<string, any>;
    by_source_after: Record<string, any>;
    reader_counts: Record<string, any>;
    warnings: string[];
    info_messages: string[];
    integrity_checks: any[];
    sources_affected?: string[];
    alert: boolean;
    is_no_change: boolean;
    /** Per-phase step results; null for runs persisted before step tracking (A3b). */
    steps?: PipelineStepResult[] | null;
}

// ── Pipeline status panel (A3b) — contract: docs/api-specs/operations-pipeline.md ──

export interface PipelinePhase {
    phase_id: string;            // "P0".."P8"
    name: string;
    description: string;
    tables_read: string[];
    tables_written: string[];
}

export interface PipelineStepResult {
    phase_id: string;            // "P0".."P8"
    name: string;
    status: 'ok' | 'failed';
    duration_ms: number;
    error: string | null;
}

export interface PipelineLastRun {
    id: string;
    timestamp: string;
    integrity_result: string;    // "13/14"
    integrity_status: 'ok' | 'degraded' | 'failed';
    net_worth_after: number | null;
    net_worth_change_pct: number | null;
    warning_count: number;
    alert: boolean;
    is_no_change: boolean;
    /** null for runs persisted before the steps column existed. */
    steps: PipelineStepResult[] | null;
}

export interface SourceFreshness {
    source_system: string;
    display_name: string;
    active_assets: number;
    latest_snapshot: string;     // "YYYY-MM-DD"
    snapshot_age_days: number;
    total_value_cny: number;
    last_price_refresh: string | null;
    price_refreshed_assets: number;
    staleness: 'fresh' | 'aging' | 'stale';
}

export interface PipelineStatusResponse {
    phases: PipelinePhase[];
    last_run: PipelineLastRun | null;
    sources: SourceFreshness[];
    generated_at: string;
}

export interface SentimentIndicator {
    indicator_key: string;
    section: string;
    indicator_name: string;
    value: number | null;
    display_value: string;
    zone: string;
    zone_color: string;
    description: string;
    /** ISO timestamp of when this value was last successfully fetched. */
    updated_at?: string | null;
    /** True when the displayed value is from a prior good fetch; live refresh failed. */
    is_stale?: boolean;
    /** ISO timestamp of the last refresh attempt (whether it succeeded or not). */
    last_refresh_attempt?: string | null;
    /** Human-readable error from the last failed fetch attempt. */
    error_detail?: string | null;
}

export interface SentimentResponse {
    last_updated: string | null;
    indicators: SentimentIndicator[];
}

// --- Audit & Sync (V2) Interfaces ---

export interface IntegrityCheck {
    name: string;
    passed: boolean;
    actual_value: string;
    threshold: string;
    details: string;
    category?: string;
    /** True if a failure of this check blocks sync success; false = advisory only. */
    blocking?: boolean;
}

export interface IntegrityStatus {
    all_passed: boolean;
    passed_count: number;
    total_count: number;
    run_at: string;
    checks: IntegrityCheck[];
}

export interface SyncAuditSummary {
    id: string;
    created_at: string;
    report_type: string;
    net_worth_before: number;
    net_worth_after: number;
    net_worth_change_pct: number;
    integrity_passed: number;
    integrity_total: number;
    alert: boolean;
}

export interface SyncAuditDetail extends SyncAuditSummary {
    asset_count_before: number;
    asset_count_after: number;
    by_source_before: Record<string, { count: number; value: number }>;
    by_source_after: Record<string, { count: number; value: number }>;
    integrity_checks: IntegrityCheck[];
    reader_counts: Record<string, { read: number; inserted: number }>;
    warnings: string[];
}

export interface AssetAuditDetail {
    asset_id: string;
    asset_name: string;
    status: string;
    reader_value: number;
    db_value: number;
    reader_qty: number;
    db_qty: number;
    original_currency: string;
    original_value: number;
    db_currency: string;
}

export interface SourceDiscrepancy {
    source_system: string;
    status: string;
    reader_asset_count: number;
    db_asset_count: number;
    reader_total_value: number;
    db_total_value: number;
    value_diff_pct: number;
    missing_in_db: string[];
    missing_in_reader: string[];
    value_mismatches: Array<{ asset_id: string; reader_value: number; db_value: number; diff_pct: number }>;
    assets: AssetAuditDetail[];
}

export interface OnDemandAuditResult {
    report_id: string;
    source_discrepancies: SourceDiscrepancy[];
    integrity: IntegrityStatus;
    overall_status: string;
}

// AI Advisor interfaces
export interface LLMSettings {
  primary_model: string;
  fallback_models: string[];
  temperature: number;
  max_output_tokens: number;
}

export interface ContextTierConfig {
    enabled: boolean;
    detail: 'summary' | 'detailed' | 'full';
    timeframe?: string; // for transactions and strategy tiers
}

export type TransactionTimeframe = '14d' | '30d' | '6m' | '1y' | 'all';

export interface ContextConfig {
  tiers: {
    identity: ContextTierConfig;
    portfolio: ContextTierConfig;
    market: ContextTierConfig;
    strategy: ContextTierConfig;
    transactions: ContextTierConfig & { timeframe: TransactionTimeframe };
  };
  include_realtime: boolean;
  include_non_rebalanceable?: boolean;
}

export interface ContextRenderResponse {
  report_type: string;
  context_text: string;
  token_estimate: Record<string, TokenEstimate | number>;
  warnings: string[];
}

export interface BriefSection {
  narrative: string;
  [key: string]: unknown;
}

export interface BriefResponse {
  id: number | null;
  report_type: string;
  content_json: Record<string, BriefSection>;
  content_markdown: string;
  model_used: string;
  created_at: string;
  context_config: ContextConfig;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  prompt_text?: string;
  raw_response_text?: string;
}

export interface BriefHistoryItem {
  id: number;
  title: string | null;
  model_used: string | null;
  created_at: string;
}

export interface TokenEstimate {
  enabled: boolean;
  detail: string;
  estimated_tokens: number;
}

// ── Review interfaces ──────────────────────────────────────────────────────

export interface ReviewQuestion {
  id: number;
  question: string;
  context: string;
}

export interface ReviewAnswer {
  question: string;
  answer: string;
}

export interface ReviewSection {
    narrative: string;
    [key: string]: unknown;
}

export interface ReviewDetailBase {
  id: number;
  content_json: Record<string, ReviewSection>;
  model_used: string;
  created_at: string;
  title: string | null;
  period_start: string | null;
  period_end: string | null;
  prompt_text: string | null;
  raw_response_text: string | null;
}

export interface ReviewResponse {
  id: number | null;
  report_type: string;
  content_json: Record<string, ReviewSection>;
  content_markdown: string;
  model_used: string;
  created_at: string;
  title?: string | null;
  period_start: string | null;
  period_end: string | null;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  prompt_text?: string;
  raw_response_text?: string;
}

export interface ReviewLatestResponse extends ReviewDetailBase {}

export interface ReviewByIdResponse extends ReviewDetailBase {
  content_markdown: string;
}

export type ReviewDetailResponse = ReviewLatestResponse | ReviewByIdResponse;

export interface ReviewHistoryItem {
  id: number;
  title: string | null;
  model_used: string | null;
  created_at: string;
  period_start: string | null;
  period_end: string | null;
}

export interface ReviewUpdatePayload {
  title?: string;
  content_json?: Record<string, ReviewSection>;
}

// ── Behavioral Metrics interfaces ──────────────────────────────────────────

export interface BehavioralMetric {
  dimension: string;
  score: number;       // 0.0-1.0
  raw_value: number;
  label: string;
  description: string;
  window_days?: number;
  computed_at?: string;
  // F5 contrarian decomposition (PRD 2026-07-07): systematic_contrarian /
  // manual_contrarian carry {untagged_count, excluded_no_price_count, alert?}.
  // Legacy contrarian_tendency carries {deprecated: true, replaced_by: [...]}.
  metadata?: Record<string, unknown> | null;
}

export interface BehavioralMetricsResponse {
  window_days: number;
  metrics: BehavioralMetric[];
}

// ── Insight interfaces ─────────────────────────────────────────────────────

export interface InsightItem {
  id: number;
  category: string;
  title: string;
  body: string;
  tags: string;
  confidence: number;
  status: string;
  recurrence_count: number;
  entity_refs: string;
  source_report_id: number | null;
  created_at: string;
  updated_at: string;
  // F6 Insight Library governance (PRD 2026-07-07) — additive fields.
  validated_cases: number;
  validated_case_links: string | Array<{ link: string; note: string | null; added_at: string }>;
  rule_layer: 'principle' | 'checklist_item' | null;
  promote_eligible: boolean;
  promote_blocked_reason: string | null;
}

export interface ValidatedCaseResponse {
  insight_id: number;
  validated_cases: number;
  validated_case_links: Array<{ link: string; note: string | null; added_at: string }>;
}

export interface RuleCitation {
  id: number;
  insight_id: number;
  memo_id: string;
  cited_at: string;
  quarter: string;
  note: string | null;
}

export interface GovernanceReport {
  year: number;
  quarter: number;
  quarter_label: string;
  promoted_this_quarter: number;
  zero_citation_rules: Array<{ id: number; title: string }>;
  pairing_warning: boolean;
  basis: string;
}

// ---------------------------------------------------------------------------
// Settings API (V4.3.2)
// ---------------------------------------------------------------------------

export interface LLMChannel {
  name: string;
  provider: string;
  enabled: boolean;
  api_key_env: string;
  key_status: 'configured' | 'missing';
  models: string[];
}

export interface LLMChannelUpdate {
  name: string;
  provider: string;
  enabled: boolean;
  api_key_env: string;
  api_key_value?: string | null;
  models: string[];
}

export interface FullLLMSettings {
  channels: LLMChannel[];
  primary_model: string;
  fallback_models: string[];
  temperature: number;
  max_output_tokens: number;
}

export interface FullLLMSettingsUpdate {
  channels: LLMChannelUpdate[];
  primary_model: string;
  fallback_models: string[];
  temperature: number;
  max_output_tokens: number;
}

export interface ChannelTestResult {
  success: boolean;
  model: string;
  latency_ms: number | null;
  error: string | null;
}

// --- Prompt Management ---
export interface PromptBlock {
  text: string;
  version: number;
  updated_at: string | null;
}

export interface PromptsData {
  shared_persona: PromptBlock;
  brief_instructions: PromptBlock;
  review_instructions: PromptBlock;
  review_questions: PromptBlock;
  using_defaults: boolean;
}

export interface PromptUpdatePayload {
  shared_persona?: string | null;
  brief_instructions?: string | null;
  review_instructions?: string | null;
  review_questions?: string | null;
}

export interface PromptPreviewResult {
  composed_prompt: string;
  current_prompt: string;
  prompt_hash: string;
}

// --- Data Sources ---
export interface SourceFilePatterns {
  workbook?: string;
  positions?: string;
  transactions?: string;
  [key: string]: string | undefined;
}

export interface SourceLastUpdate {
  origin: 'upload' | 'fetch';
  at: string; // ISO-8601 UTC
}

export interface SourceConfig {
  key: string;
  enabled: boolean;
  reader: string;
  data_dir: string | null;
  file_patterns: SourceFilePatterns;
  asset_prefixes: string[];
  // Enriched:
  resolved_dir: string | null;
  fallback_active: boolean;
  file_found: boolean;
  file_path: string | null;
  file_size_bytes: number | null;
  file_modified: string | null;
  resolved_files?: Record<string, string>;
  // C5 additions:
  label: string;
  authority: 'authoritative' | 'co-authority' | 'non-authoritative' | 'historical-shadow';
  authority_note: string | null;
  format: 'csv' | 'xlsx' | 'flex_csv';
  can_fetch: boolean;
  last_update: SourceLastUpdate | null;
  // WS-A A3 (reader-mapping-management): cheap unmapped-column count, computed
  // ONLY for financial_summary. null for every other reader, and null whenever
  // the count can't be cheaply computed (file missing/unreadable).
  unmapped_count: number | null;
}

export interface SourceRegistryResponse {
  sources: SourceConfig[];
  fallback_dir: string | null;
}

export interface SourceConfigUpdate {
  key: string;
  enabled?: boolean;
  data_dir?: string; // empty string = clear to null
  file_patterns?: Record<string, string>;
}

export interface SourceRegistryUpdateRequest {
  sources: SourceConfigUpdate[];
}

export interface SourceTestResult {
  reader: string;
  file_found: boolean;
  file_path: string | null;
  is_valid: boolean;
  warnings: string[];
  file_type: string | null;
  file_size_bytes: number | null;
  file_modified: string | null;
}

export interface UploadResult {
  reader: string;
  file_path: string;
  file_size_bytes: number;
  is_valid: boolean;
  warnings: string[];
  file_type: string | null;
}

export interface SourceEvent {
  id: number;
  reader: string;
  origin: 'upload' | 'fetch';
  filename: string;
  file_size_bytes: number | null;
  occurred_at: string; // ISO-8601 UTC
  is_valid: boolean | null;
  warnings: string[];
  previous_filename: string | null;
}

export interface SourceEventsResponse {
  reader: string | null;
  events: SourceEvent[];
  total_count: number;
}

export interface FetchResult {
  reader: string;
  file_path: string;
  file_size_bytes: number;
  line_count: number;
  fetched_at: string; // ISO-8601 UTC
  pruned: string[]; // filenames removed by retention
}

export interface SourceFileEntry {
  filename: string;
  file_path: string;
  file_size_bytes: number;
  file_modified: string;
  is_active: boolean;
}

export interface SourceFilesResponse {
  reader: string;
  directory: string;
  files: SourceFileEntry[];
  total_count: number;
}

export interface SyncStatus {
  running: boolean;
  started_at: string | null;
}

export interface SourceHealthEntry {
  reader: string;
  last_sync_at: string | null;
  row_count: number | null;
  net_value_cny: number | null;
  file_path: string | null;
  file_modified: string | null;
  file_size_bytes: number | null;
  file_stale: boolean;
  status: 'ok' | 'stale' | 'pending_sync' | 'missing' | 'never_synced' | 'unknown';
}

export interface SourceHealthResponse {
  sources: SourceHealthEntry[];
  last_sync_at: string | null;
  all_healthy: boolean;
}

export interface MarketDataProvider {
  market: 'us' | 'cn_fund' | string;
  fetcher: 'yfinance' | 'akshare' | string;
  asset_count: number;
  status: 'active' | string;
}

export interface MarketDataRefreshedAsset {
  asset_id: string;
  code: string;
  market: 'us' | 'cn_fund' | string;
  price: number;
  as_of_date: string;
  source: string;
}

export interface MarketDataSkippedAsset {
  asset_id: string;
  market: 'us' | 'cn_fund' | 'unknown' | string;
  reason: string;
}

export interface MarketDataErrorAsset {
  asset_id: string;
  market: 'us' | 'cn_fund' | 'unknown' | string;
  reason: string;
}

export interface MarketDataRefreshResult {
  refreshed: number;
  skipped: number;
  errors: number;
  holdings_updated: number;
  fx_rates?: Record<string, number>;
  refreshed_assets?: MarketDataRefreshedAsset[];
  skipped_assets?: MarketDataSkippedAsset[];
  error_assets?: MarketDataErrorAsset[];
  timestamp: string;
}

export interface MarketDataStatusResponse {
  last_refresh: MarketDataRefreshResult | null;
  providers: MarketDataProvider[];
  staleness: 'fresh' | 'aging' | 'stale' | 'never';
}

export interface UploadHistoryEntry {
  id: number;
  reader: string;
  filename: string;
  file_size_bytes: number | null;
  uploaded_at: string;
  is_valid: boolean | null;
  warnings: string[];
  previous_filename: string | null;
}

export interface UploadHistoryResponse {
  reader: string | null;
  entries: UploadHistoryEntry[];
  total_count: number;
}

// ---------------------------------------------------------------------------
// Asset Analysis (Phase 4 — DSA)
// ---------------------------------------------------------------------------

export interface AnalyzableAssetSearchResult {
  code: string;
  name?: string;
  in_portfolio: boolean;
  position_pct?: number;
}

export interface AnalysisResult {
  id: number;
  asset_code: string;
  asset_name?: string;
  technical_signals: Record<string, unknown>;
  llm_analysis: Record<string, unknown>;
  llm_analysis_markdown: string;
  portfolio_context: Record<string, unknown>;
  model_used: string;
  data_source: string;
  triggered_by: string;
  created_at: string;
}

export interface AnalysisHistoryItem {
  id: number;
  asset_code: string;
  asset_name?: string;
  timing_signal?: string;
  confidence?: number;
  created_at: string;
  model_used?: string;
  data_source?: string;
}

export interface ImportAdapterRun {
  run_id: number;
  headers: string[];
  inferred_mapping: Record<string, string>;
  preview_rows: Record<string, unknown>[];
  total_rows: number;
}

export interface ImportAdapterValidationResponse {
  valid: boolean;
  warnings: string[];
  errors: string[];
  row_counts: { total: number };
}

export interface ImportAdapterApprovalRequest {
  source_system: string;
  asset_prefixes: string[];
  authority_priority: number;
  approved_by?: string;
  generate_reader?: boolean;
  display_name?: string;
}

export interface ImportAdapterApprovalResponse {
  ok: boolean;
  generated_reader_key?: string;
  reader_warning?: string;
}

export interface ImportAdapterStagedRow {
  row_index: number;
  row_kind: string;
  payload: Record<string, unknown>;
  validation_status: string;
  messages: string[];
}

// ---------------------------------------------------------------------------
// Decision Intelligence Feedback Loop — Phase 1 types (Steps 10–12)
// ---------------------------------------------------------------------------

export interface PendingVerificationItem {
  id: number;
  log_date: string;
  asset_id: string;
  asset_name: string | null;
  action: string;
  price: number | null;
  quantity: number | null;
  amount_cny: number | null;
  decision_reason: string | null;
  suggestion_source: string | null;
  verification_status: 'pending' | 'pending_window' | 'verified' | 'verification_blocked';
  is_matured: boolean;
  outcome_pct_preview: number | null;
  suggested_verdict: 'good_call' | 'regret' | 'bullet_dodged' | 'missed_opportunity' | null;
  linked_insight_id: number | null;
  linked_insight_title: string | null;
  updated_at: string;
  verdict: 'good_call' | 'regret' | 'bullet_dodged' | 'missed_opportunity' | null;
  outcome_pct: number | null;
  verification_result: string | null;
  verification_date: string | null;
  /** Interim price-change % from log_date to today's most recent close. Non-null only for
   *  non-matured rows where a baseline + a later price both exist. Sign-flipped for sells
   *  (same convention as outcome_pct_preview). */
  outcome_to_date_pct: number | null;
  /** ISO date (YYYY-MM-DD) of the most-recent close used in outcome_to_date_pct. */
  outcome_to_date_asof: string | null;
}

export interface MemoProposal {
  section: string;
  current_text: string;
  proposed_text: string;
  rationale: string;
}

export interface MemoProposalResult {
  proposals: MemoProposal[];
  report_id: number;
  model_used: string;
  memo_id: number;
  generated_at: string;
}

export interface InsightTradeLink {
  id: number;
  insight_id: number;
  trade_id: number;
  link_type: 'auto_source' | 'manual';
  confidence: number | null;
  rationale: string | null;
  created_at: string;
  trade_log_date: string | null;
  trade_asset_id: string | null;
  trade_action: string | null;
}

export interface VerifyTradeBody {
  verification_result: string;
  verification_date?: string;
  verdict?: 'good_call' | 'regret' | 'bullet_dodged' | 'missed_opportunity';
  expected_updated_at?: string;
}

export interface CrossCheckAuditResult {
  audit_markdown: string;
  summary: Record<string, unknown>;
  model_used: string;
  generated_at: string;
  report_id: number;
}

// ---------------------------------------------------------------------------
// LLM Usage (ADR-010 WS1)
// ---------------------------------------------------------------------------

export interface LLMUsageRow {
  model_used: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  success_calls: number;
  failure_calls: number;
  last_used: string | null;
}

export interface LLMUsageResponse {
  models: LLMUsageRow[];
  total_calls: number;
  total_tokens: number;
  total_cost_usd: number;
}

// ---------------------------------------------------------------------------
// Top Movers — GET /performance/movers (GitHub #27)
// ---------------------------------------------------------------------------

/** One row in the movers response — asset level or class level. */
export interface MoverRow {
    /** asset_id at asset level; sub_class or top_class name at class levels. */
    key: string;
    /** Registry display name (asset level) or class name (class levels). */
    name: string;
    /** Present at asset + sub_class levels. */
    top_class?: string;
    /** Present at asset + sub_class levels. */
    sub_class?: string;
    /** (p_now / p_then − 1) × 100, negative for a decline. */
    pct_change: number;
    /** CNY P&L impact: mv_now × (1 − p_then/p_now). Negative = loss. */
    pl_impact_cny: number;
    market_value: number;
    /** False when the asset has no close ≤ window_start (partial coverage). */
    window_covered: boolean;
    /** Always 1 at asset level; >1 at class levels. */
    asset_count: number;
}

export interface MoversResponse {
    window: string;          // "7d" | "30d" | "3m" | "6m" | "12m"
    window_start: string;    // ISO date
    level: string;           // "asset" | "sub_class" | "top_class"
    movers: MoverRow[];
    excluded_unpriced_count: number;
}

// ---------------------------------------------------------------------------
// Value-trap reviews — PRD 2026-07-07 F2 (loss-side mandatory review trigger)
// GET/PUT /reviews/value-trap, POST /reviews/value-trap/scan
// ---------------------------------------------------------------------------

export type ValueTrapRuling = 'hold_with_thesis' | 'trim' | 'liquidate';
export type ValueTrapStatus = 'open' | 'ruled';

export interface ValueTrapReview {
    id: number;
    asset_id: string;
    asset_name: string | null;
    status: ValueTrapStatus;
    trigger_threshold_pct: number;
    unrealized_return_pct: number | null;
    memo_id: string | null;
    opened_at: string | null;
    refreshed_at: string | null;
    thesis_restated: string | null;
    falsification_check: string | null;
    would_buy_today: string | null;
    ruling: ValueTrapRuling | null;
    adversarial_ack: boolean;
    next_review_date: string | null;
    last_reviewed_at: string | null;
    last_ruling: ValueTrapRuling | null;
    next_trigger_threshold_pct: number | null;
    /** Server-computed (F2.4). */
    days_open: number | null;
    overdue: boolean;
}

/** R2-1: per-deferred-asset detail returned in the scan summary. */
export interface DeferredAssetEntry {
    asset_id: string;
    price_date: string | null;
    freshness_class: string;
    data_fix_id: number | null;
    /** ISO date YYYY-MM-DD; null when no data_fix (slow/none-class assets). */
    data_fix_due_at: string | null;
}

export interface ValueTrapScanSummary {
    scanned: number;
    hits: number;
    opened: number;
    refreshed: number;
    skipped_bucket: number;
    skipped_no_cost: number;
    /** Assets that passed all gates and had a return computed (cost > 0, not stale). */
    evaluated: number;
    /** Assets whose valuation freshness (max of snapshot_date, price_updated_at) is stale. */
    deferred_unreliable: number;
    /** R2-1: Cash-like assets exempt from the scan (deposits, money-market, bank wealth). */
    exempt_cash_like: number;
    /** R2-1: Per-deferred-asset detail for the frontend Deferred tab. */
    deferred_assets: DeferredAssetEntry[];
}

export interface ValueTrapPendingCount {
    open: number;
    overdue: number;
}

export interface ValueTrapRulingSubmission {
    thesis_restated?: string;
    falsification_check?: string;
    would_buy_today?: string;
    ruling: ValueTrapRuling;
    adversarial_ack?: boolean;
    next_review_date?: string;
    /** Fix 2: required true when asset has unresolved memo linkage. */
    linkage_ack?: boolean;
}

// WS2 — value-trap context panel + AI draft (F2.3 enrichment)

export interface ValueTrapDecisionHistoryEntry {
    log_date: string | null;
    action: string | null;
    quantity: number | null;
    price: number | null;
    rule_bucket: string | null;
    verification_status: string | null;
}

/** Fix 2: three-state memo linkage for the context panel. */
export type ValueTrapLinkageState = 'linked' | 'confirmed_none' | 'unresolved';

export interface ValueTrapMemoEntry {
    memo_id: string;
    title: string;
    falsification_summary: string | null;
}

/** R2-1: price freshness verdict embedded in the context position. */
export interface PositionFreshnessVerdict {
    price_date: string | null;
    price: number | null;
    freshness_class: string;
    fresh: boolean;
}

/** R2-3: one open FIFO lot. */
export interface LotEntry {
    date: string;
    quantity: number;
    price_unit: number;
}

/** R2-3: lot-granular cost basis detail for the context panel. */
export interface LotDetail {
    open_lot_count: number;
    open_qty: number;
    avg_cost: number;
    /** Capped at 200 entries; check `truncated` flag. */
    lots: LotEntry[];
    truncated: boolean;
}

export interface ValueTrapContext {
    review_id: number;
    asset_id: string;
    position: {
        qty: number;
        cost_price_unit: number;
        market_price_unit: number;
        price: number;              // R2-1: alias for market_price_unit
        market_value: number;
        snapshot_date: string | null;
        currency: string;
        price_date: string | null;  // R2-1: GREATEST(snapshot_date, price_updated_at)
        freshness: PositionFreshnessVerdict | null; // R2-1: full freshness verdict
    } | null;
    loss: {
        unrealized_return_pct: number | null;
        trigger_threshold_pct: number;
        days_open: number | null;
    };
    /** Fix 2: replaces originating_memo — three-state linkage. */
    memo_linkage: {
        state: ValueTrapLinkageState;
        memos: ValueTrapMemoEntry[];
        /** Non-null for confirmed_none and unresolved states. */
        display_text: string | null;
    };
    decision_history: ValueTrapDecisionHistoryEntry[];
    /** R2-3: total trade_logs count for the asset (>= len(decision_history)). */
    decision_history_total: number;
    /** R2-3: FIFO open-lot breakdown; null when no transaction history. */
    lot_detail: LotDetail | null;
    case_file: {
        asset_id: string;
    };
}

export interface ValueTrapDraft {
    thesis_draft: string;
    falsification_draft: string;
    buy_today_draft: string;
    model: string;
}

// ─────────────────────────────────────────────────────────────────────────
// North Star panel (PRD 2026-07-07 F3, Batch B6)
// ─────────────────────────────────────────────────────────────────────────

export type CashFlowClassification = 'external_contribution' | 'internal_transfer' | 'income_reinvested';

export interface ContributionMonthlyPoint {
    month: string;
    amount: number;
}

export interface ContributionMetrics {
    ytd_sum: number;
    trailing_12m_sum: number;
    monthly_series: ContributionMonthlyPoint[];
    unclassified_count: number;
}

export interface UnclassifiedFlow {
    source_table: 'transactions' | 'income_expense_monthly' | 'fs_cash_delta';
    source_row_key: string;
    amount_cny: number;
    flow_date: string | null;
    transaction_type: string | null;
    asset_id: string | null;
}

export interface FlowTagRequest {
    source_table: 'transactions' | 'income_expense_monthly' | 'fs_cash_delta';
    source_row_key: string;
    classification: CashFlowClassification;
    note?: string;
}

export interface FlowTagResult {
    source_table: string;
    source_row_key: string;
    classification: CashFlowClassification;
    tagged_by: 'heuristic' | 'manual';
    amount_cny: number | null;
    flow_date: string | null;
    note: string | null;
}

export interface FlowClassifySummary {
    tagged: number;
    skipped_manual: number;
    unclassified_count: number;
    /** IDs of cash_flow_tags rows created/updated in this run (for Undo). */
    tagged_ids?: number[];
    /** true when dry_run=true was passed */
    dry_run?: boolean;
    /** How many rows would be tagged (only set when dry_run=true). */
    would_tag?: number | null;
}

/** One already-classified flow row — from GET /north-star/flows/classified */
export interface ClassifiedFlow {
    source_table: 'transactions' | 'income_expense_monthly' | 'fs_cash_delta';
    source_row_key: string;
    classification: CashFlowClassification;
    tagged_by: 'heuristic' | 'manual';
    amount_cny: number | null;
    flow_date: string | null;
    asset_id: string | null;
    transaction_type: string | null;
    rule_id: string | null;
    note: string | null;
    /** V81: true when the underlying transaction can no longer be resolved
     *  (re-imported with a different identity, or genuinely deleted) — the
     *  tag itself (classification/tagged_by/flow_date/note) is still shown,
     *  but amount_cny/asset_id/transaction_type are null. */
    orphaned?: boolean;
}

/** Request body for PUT /north-star/flows/tag/bulk */
export interface BulkTagRequest {
    items: Array<{ source_table: 'transactions' | 'income_expense_monthly' | 'fs_cash_delta'; source_row_key: string }>;
    classification: CashFlowClassification;
}

/** Response from PUT /north-star/flows/tag/bulk */
export interface BulkTagResult {
    tagged: number;
    not_found: number;
}

/** Request body for DELETE /north-star/flows/tag */
export interface UntagRequest {
    items: Array<{ source_table: 'transactions' | 'income_expense_monthly' | 'fs_cash_delta'; source_row_key: string }>;
}

/** Response from DELETE /north-star/flows/tag */
export interface UntagResult {
    deleted: number;
}

/** Per-month entry in ContributionsSummary.investment.series (投资理财-derived).
 *
 * `by_destination`'s keys are NOT a fixed set — the backend derives them from
 * the `ie_column` mapping's destination buckets (`us_ibkr` was added
 * 2026-08-01), and every declared bucket is always present (0.0 when the month
 * has no money in it). Consumers must iterate the keys they receive; a
 * hardcoded key list silently drops a destination from any total it builds.
 */
export interface InvestmentContributionMonth {
    month: string;
    by_destination: Record<string, number>;
    gross_invested: number;
    redemptions: number;
    /** Σ role='income' over the ledger's LEAF income columns — the denominator
     * basis of both TTM rates. Excludes redemptions and the 报销 pass-through. */
    income_basis: number;
    /** Σ role='expense' (必要/非必要开支 leaves). Excludes role='invested'
     * (investing is not spending) and the 工作开支 pass-through. */
    expense_basis: number;
    /** 报销 in / 工作开支 out — two ends of the same money, excluded from both bases. */
    pass_through_in: number;
    pass_through_out: number;
    /** Excel-equivalent 总收入合计, DERIVED from the leaves — never a denominator. */
    income: number;
}

/**
 * Authoritative 月度收支-derived portfolio contribution/savings figure (plan
 * 2026-07-20-investment-contributions-savings.md §Reconciliation). This is a
 * DIFFERENT source than ytd_sum/trailing_12m_sum/by_classification above
 * (cash_flow_tags-derived per-row flow view) — never sum the two.
 */
export interface InvestmentContributionsSummary {
    series: InvestmentContributionMonth[];
    gross_invested_ttm: number;
    redemptions_ttm: number;
    /** Excel-equivalent 总收入合计, DERIVED by Huinsight from the ledger's leaf columns
     * (owner ruling 2026-08-01: no Excel-computed aggregate is a calculation
     * input). It is NOT the denominator of either rate — `income_basis_ttm` is. */
    income_ttm: number;
    net_external_ttm: number;
    internal_realloc_ttm: number;
    /** Denominator of BOTH rates: Σ(role='income', CNY) over the window's leaf
     * columns. Excludes redemptions and both ends of the 报销/工作开支 round trip. */
    income_basis_ttm: number;
    /** Σ(role='expense', CNY) — 必要/非必要开支 leaves. Excludes role='invested'. */
    expense_basis_ttm: number;
    /** Vested in-window and still held (specific-lot), CNY. */
    rsu_retained_ttm: number;
    /** net_external_ttm + rsu_retained_ttm — the numerator of investment_rate_ttm. */
    investment_numerator_ttm: number;
    pass_through_in_ttm: number;
    pass_through_out_ttm: number;
    /**
     * TRUE savings rate — everything not spent:
     * `(income_basis_ttm − expense_basis_ttm) / income_basis_ttm` (60.25% live).
     *
     * Its meaning CHANGED on 2026-08-01 (ADR-025 Amendment, plan
     * 2026-08-01-ie-column-mapping-and-ibkr-amounts.md §WS-G). What this field
     * used to carry — net new money that reached an investment account ÷ income —
     * is now `investment_rate_ttm`. The two are ~19pp apart on live data, so
     * NEVER render one under the other's caption. `null` when the window has no
     * income basis (render an em-dash, never 0%).
     */
    savings_rate_ttm: number | null;
    /**
     * The share of income that reached an investment account:
     * `(net_external_ttm + rsu_retained_ttm) / income_basis_ttm` (41.56% live).
     * `null` when the window has no income basis.
     */
    investment_rate_ttm: number | null;
    /** Saved but not yet deployed, CNY: the two rates' difference expressed as
     * money (`income_basis − expense_basis − investment_numerator`). */
    undeployed_cash_ttm: number;
    /** Keys are the `ie_column` mapping's destination buckets, not a fixed set
     * (`us_ibkr` added 2026-08-01) — iterate what you receive. */
    by_destination_ttm: Record<string, number>;
    /** null (never a fabricated month) when the ledger window is empty. */
    window_start_month: string | null;
    window_end_month: string | null;
    /** W-5 (docs/design/2026-07-26-your-path.dc.html.md §3.1) — PARTICIPATION
     * signal, not a per-month amount: count of months in this same window
     * with any non-zero investment inflow, out of
     * months_with_contribution_window (the true number of months examined —
     * may be less than the requested window when less history exists; never
     * padded). Both null (never fabricated) when the window is empty. */
    months_with_contribution: number | null;
    months_with_contribution_window: number | null;
}

/** Query param accepted by GET /north-star/contributions — the Cash Flow tab's
 * Last 12m / 36m / All Time toggle. Only affects investment.* and rsu.* (see
 * ContributionsSummary docstring); ytd_sum/trailing_12m_sum/by_classification
 * are always trailing-12M/YTD (ADR-025 §4a legacy figures, retired from
 * display, never window-toggled). */
export type ContributionsWindow = '12' | '36' | 'all';

/**
 * RSU vested/retained portfolio-inflow figure (plan
 * 2026-07-25-cash-flow-classification-completion.md §3.3, §5.1 — owner
 * decision: "RSU gets its own line so it's clear to see both"). This is a
 * THIRD source, distinct from BOTH investment.* (月度收支 投资理财, above)
 * AND ytd_sum/trailing_12m_sum/by_classification (cash_flow_tags-derived).
 * `rsu.vest_gross_ttm` is gross RSU vested (before any sell); `retained_ttm`
 * is FIFO-derived — shares vested inside the window and still held today,
 * valued at their own vest price. NEVER sum any of these three sources —
 * the 月度收支 ledger already books RSU vests as income and any reinvested
 * sale proceeds as 投资理财, so summing rsu.* on top of investment.* or
 * trailing_12m_sum double-counts the same money.
 */
export interface RsuContributionsSummary {
    vest_gross_ttm: number;
    retained_ttm: number;
    retained_shares: number;
    /** Total FIFO over-sold quantity across all RSU_Excel history (data-health
     * signal, not window-scoped). 0 when clean. > 0 means a sell exceeded all
     * open lots for that asset — a data error (e.g. a mis-dated transaction)
     * — and retained_ttm/retained_shares may be understated by this amount. */
    oversold_shares: number;
    window_start_month: string | null;
    window_end_month: string | null;
}

/** Response from GET /north-star/contributions */
export interface ContributionsSummary {
    ytd_sum: number;
    trailing_12m_sum: number;
    unclassified_count: number;
    by_classification: {
        external_contribution: number;
        internal_transfer: number;
        income_reinvested: number;
    };
    investment?: InvestmentContributionsSummary;
    rsu?: RsuContributionsSummary;
}

export interface TimeInMarketWeight {
    month: string;
    weight_pct: number;
}

export interface TimeInMarket {
    insufficient_data: boolean;
    reason?: string;
    ratio?: number;
    in_market_months?: number;
    total_months?: number;
    target_pct?: number;
    band_floor_pct?: number;
    monthly_weights?: TimeInMarketWeight[];
}

export interface CostEditEntry {
    ts: string;
    old: number | null;
    new: number | null;
}

export interface UnforcedError {
    id: number;
    error_date: string | null;
    description: string;
    est_cost_cny: number | null;
    root_cause: string | null;
    linked_rule: string | null;
    created_at: string | null;
    cost_edit_history?: CostEditEntry[];
}

export interface UnforcedErrorCreate {
    error_date: string;
    description: string;
    est_cost_cny?: number;
    root_cause?: string;
    linked_rule?: string;
}

export interface GlidePathAssumptions {
    current_nw: number;
    trailing_twr_pct: number | null;
    monthly_contribution: number;
    target: number;
    note: string;
    /** "annualized TWR, rebalanceable assets (Performance-page filter)" */
    twr_basis?: string;
    /**
     * "trailing-12M average of flows tagged external_contribution"
     * (Fix 5, 2026-07-10 — changed from income_expense_monthly 投资理财 source)
     */
    run_rate_basis?: string;
    /**
     * The actual monthly run-rate value (null when contaminated or implausible).
     * (Fix 5, 2026-07-10)
     */
    current_run_rate_monthly?: number | null;
    /**
     * Explains run-rate availability:
     *   "available" | "pending flow classification (N untagged)" |
     *   "run-rate implausible — check flow tagging"
     * (Fix 5, 2026-07-10)
     */
    run_rate_status?: string;
}

export interface RequiredCagrRow {
    horizon_years: number;
    required_cagr_pct: {
        zero: number | null;
        current_run_rate: number | null;
        scenario: number | null;
    };
}

/**
 * Headline binding (Fix 5, 2026-07-10): headline number and text must come
 * from the same scenario.
 */
export interface GlidePathHeadline {
    /** Years to target computed at headline_contribution_monthly. */
    years_to_target: number | null;
    /** The contribution level used for the headline (0 or run-rate). */
    contribution_monthly: number;
    /** Which scenario drives the headline: "zero" or "current_run_rate". */
    scenario_used: 'zero' | 'current_run_rate';
}

export interface GlidePath {
    reachable: boolean;
    insufficient_data?: boolean;
    /** Years to target at the *scenario* (monthly_contribution param) level. */
    years_to_target?: number | null;
    /**
     * R2-4: years to target at each contribution level (same deterministic engine).
     * scenario is null when monthly_contribution == 0 (same as zero).
     * run_rate is null when run-rate is unavailable.
     */
    years_to_target_by_scenario?: {
        zero: number | null;
        run_rate: number | null;
        scenario: number | null;
    };
    /**
     * Headline binding (Fix 5): use this sub-dict for the headline display.
     * headline.years_to_target == the named column's cell value.
     */
    headline?: GlidePathHeadline;
    /** Null when flow dataset is contaminated or run-rate is implausible. */
    run_rate_monthly?: number | null;
    /** Status string for the run-rate column header (Fix 5). */
    run_rate_status?: string;
    required_cagr_grid?: RequiredCagrRow[];
    assumptions: GlidePathAssumptions;
}

export interface NorthStarPanel {
    contributions: ContributionMetrics;
    time_in_market: TimeInMarket;
    unforced_errors: UnforcedError[];
    glide_path: GlidePath;
}

// ── Reader Mapping Management (ADR-023/ADR-023 — WS-A/WS-B/WS-C) ──────────
// docs/api-specs/reader-mappings.md. Managed today:
//   financial_summary / fs_column        (WS-A)
//   gold, insurance, rsu / id_field_map   (WS-B)
//   schwab / known_etf, symbol_norm, action_map   (WS-C)
//   cn_fund / type_map                            (WS-C)

export interface ReaderMappingValue {
    asset_id: string;
    asset_name: string;
    currency: string;   // fs_column: always "CNY"
}

/** WS-B (gold/insurance/rsu) — a "field:label" map_key's segment value, e.g.
 *  {"code": "CMB"}. See docs/api-specs/reader-mappings.md Section C2. */
export interface IdFieldMapValue {
    code: string;
}

/** WS-C (schwab/cn_fund) vocabulary map_value shapes — one per mapping_kind. */
export interface KnownEtfValue { etf: true }
export interface SymbolNormValue { to: string }
export interface TypeMapValue { type: string }

export type AnyMappingValue =
    | ReaderMappingValue | IdFieldMapValue | KnownEtfValue | SymbolNormValue | TypeMapValue;

export interface ReaderMapping {
    id: number;
    reader_key: string;
    mapping_kind: string;
    map_key: string;              // Excel column / "field:label" / ticker / raw action-or-type label
    /** Shape depends on mapping_kind — see AnyMappingValue's members. Loosely
     *  typed here (mirrors the backend's Dict[str, Any] MappingOut.map_value)
     *  since one panel component renders several kinds. */
    map_value: Record<string, any>;
    status: 'active' | 'archived';
    sort_order: number | null;
    updated_at: string | null;    // ISO-8601
}

/** ADR-023 A4.1 — classification precedence: ignored > native > computed >
 *  liability > candidate. Only 'candidate' is genuinely actionable / counted
 *  toward the amber-chip unmapped_count. */
export type UnmappedColumnCategory = 'ignored' | 'native' | 'computed' | 'liability' | 'candidate';

export interface UnmappedColumn {
    column: string;
    /** true = native-currency sibling column (_USD/_HKD suffix) — informational
     *  only, NOT counted toward the amber-chip unmapped_count. Kept for
     *  backward compat; equivalent to category === 'native'. */
    ignored_native: boolean;
    category: UnmappedColumnCategory;
    /** Only set when category === 'ignored' — the reader_mappings row id,
     *  needed to call readerMappingsApi.unignore(reader, mapping_id). */
    mapping_id: number | null;
}

export interface ReaderMappingListResponse {
    reader: string;
    mapping_kind: string;
    mappings: ReaderMapping[];
    /** true if reader_mappings has zero rows for this (reader, kind) — pre-seed
     *  / seed-failure edge case; sync path still falls back to code defaults. */
    defaults_only: boolean;
    unmapped_columns: UnmappedColumn[];
}

export interface ReaderMappingCreateRequest {
    kind: string;
    map_key: string;
    value: Record<string, any>;
}

export interface ReaderMappingPatchValue {
    asset_name?: string;
    /** Only accepted if the CURRENT asset_id has zero holdings rows — else 409. */
    asset_id?: string;
    /** WS-B (id_field_map) / WS-C (symbol_norm's `to`, action_map/type_map's
     *  `type`) — see the kind-specific value shapes above. */
    [key: string]: any;
}

export interface ReaderMappingPatchRequest {
    value?: ReaderMappingPatchValue;
    sort_order?: number;
}

export interface ReaderMappingDeactivateHint {
    asset_id: string;
    endpoint: string;   // "/taxonomy/assets/{asset_id}"
    method: 'DELETE';
    note: string;
}

export interface ReaderMappingArchiveResponse {
    mapping: ReaderMapping;
    asset_has_holdings: boolean;
    deactivate_hint: ReaderMappingDeactivateHint | null;
}

export interface ReaderMappingDeleteResponse {
    deleted: number;
    /** fs_column: the asset_id that was mapped. Null for id_field_map/vocab
     *  kinds (see `code`). */
    asset_id: string | null;
    /** id_field_map/vocab kinds only: the code/value segment that was mapped. */
    code?: string | null;
}

export interface ReaderMappingPreviewProposedItem {
    map_key: string;
    value: ReaderMappingValue;
}

export interface ReaderMappingPreviewRequest {
    proposed?: ReaderMappingPreviewProposedItem[];
}

export interface ReaderMappingPreviewColumnResult {
    map_key: string;
    column_found: boolean;     // is map_key actually a column header in the current file?
    nonzero_rows: number;      // holdings rows produced for this asset_id by the melt
    latest_value: number | null;
    latest_date: string | null;   // ISO date (YYYY-MM-DD)
}

export interface ReaderMappingPreviewResponse {
    reader: string;
    mapping_kind: string;
    file_path: string | null;   // null if no file is currently resolved/found
    results: ReaderMappingPreviewColumnResult[];
    unmapped_columns: UnmappedColumn[];
}

export interface ReaderMappingIgnoreColumnRequest {
    map_key: string;
}

export interface ReaderMappingUnignoreResponse {
    unignored: number;
    map_key: string;
}

/** WS-B (gold/insurance/rsu) id_field_map preview — docs/api-specs/reader-mappings.md
 *  Section C2. A different response shape than fs_column's (no `results`). */
export interface IdFieldMapPreviewItem {
    field: string;
    label: string;
    map_key: string;
    mapped: boolean;
    code: string | null;
}

export interface IdFieldMapPreviewResponse {
    reader: string;
    mapping_kind: string;
    file_path: string | null;
    items: IdFieldMapPreviewItem[];
    unmapped_columns: UnmappedColumn[];
}

/** WS-C (schwab known_etf/symbol_norm/action_map, cn_fund type_map) preview —
 *  scans the reader's current file for symbols/actions/types and reports
 *  mapped-vs-unmapped against the merged vocabulary. */
export interface VocabPreviewItem {
    value: string;
    mapped: boolean;
    mapped_value: Record<string, any> | null;
}

export interface VocabPreviewResponse {
    reader: string;
    mapping_kind: string;
    file_path: string | null;
    items: VocabPreviewItem[];
    unmapped_columns: UnmappedColumn[];
}

/** Discriminate on `mapping_kind` (or presence of `results` vs `items`). */
export type AnyPreviewResponse =
    | ReaderMappingPreviewResponse | IdFieldMapPreviewResponse | VocabPreviewResponse;

/** WS-C — the fixed transaction_type enum action_map/type_map values must
 *  belong to (mirrors src.services.reader_mappings.ALLOWED_TRANSACTION_TYPES).
 *  'transfer' (WS-3.1, V79) is a pseudo-type: Schwab's 'Security Transfer'
 *  action is directionally ambiguous, so it resolves to transfer_out/
 *  transfer_in by quantity sign at the reader hook and is never persisted on
 *  a transactions row — still a valid action_map dropdown target. */
export const ALLOWED_TRANSACTION_TYPES = [
    'buy', 'sell', 'dividend', 'dividend_cash', 'dividend_reinvest', 'reinvest_dividend',
    'tax_adjustment', 'stock_split', 'transfer_in', 'transfer_out', 'transfer', 'vest', 'rsu_vest',
    'premium_payment', 'adjustment_buy', 'interest', 'other',
] as const;

// ── Attribution & Flows Program (WS-1) — docs/api-specs/attribution.md ────────

/** Roll-up granularity for GET /attribution/monthly. Drill-down floor = 'asset'. */
export type AttributionLevel = 'asset' | 'sub_class' | 'top_class' | 'total';

/** `dq_detail.kind` — see docs/api-specs/attribution.md → dq_reason / dq_detail. */
export type AttributionDqKind =
    | 'source_transition'
    | 'snapshot_lag'
    | 'first_seen'
    | 'stale_end_snapshot'
    | 'unexplained';

/** Machine-readable DQ detail — shape varies by `kind` (see spec examples),
 *  so extra fields are typed loosely via the index signature. */
export interface AttributionDqDetail {
    kind: AttributionDqKind;
    [key: string]: unknown;
}

/** One roll-up row (top_class / sub_class / asset / total) for a single month.
 *  `sub_class` is not documented in the spec's example payload but is included
 *  defensively (mirrors `top_class`) so the frontend can filter asset-level
 *  rows by their parent sub-class during drill-down without a server round trip. */
export interface AttributionRow {
    key: string;
    top_class?: string | null;
    sub_class?: string | null;
    /** Chinese companion for `key` — only populated when `key` is a class/sub-class
     *  name (top_class/sub_class rollup levels), not for level=asset (key=asset_id)
     *  or level=total (key='Total', a literal). Program BIL / WS-9. */
    key_cn?: string | null;
    top_class_cn?: string | null;
    sub_class_cn?: string | null;
    asset_id?: string;
    asset_name?: string;
    mv_start: number;
    mv_end: number;
    delta: number;
    price_effect: number;
    trade_effect: number;
    transfer_effect: number;
    income_effect: number;
    residual: number;
    dq_flag: boolean;
    /** Populated (non-null) only for `level=asset` rows with `dq_flag=true`.
     *  Rollup rows and non-flagged asset rows carry `null` (present key). */
    dq_reason?: string | null;
    dq_detail?: AttributionDqDetail | null;
    asset_count?: number;
}

export interface AttributionTotals {
    delta: number;
    price_effect: number;
    trade_effect: number;
    transfer_effect: number;
    income_effect: number;
    residual: number;
}

export interface AttributionMonthlyResponse {
    month: string;
    level: AttributionLevel;
    rows: AttributionRow[];
    totals: AttributionTotals;
    dq_flagged_assets: string[];
    computed_at: string;
}

export interface AttributionAssetEvent {
    date: string;
    type: string;
    qty: number;
    amount_cny: number;
    price: number;
}

/** One month of an asset's attribution history. `events` is populated for the
 *  expanded/most-recent month per the spec. */
export interface AttributionAssetMonthRow extends AttributionRow {
    month: string;
    events?: AttributionAssetEvent[];
}

export interface AttributionAssetHistoryResponse {
    asset_id: string;
    months: AttributionAssetMonthRow[];
}

export interface AttributionFlows {
    external_in: number;
    external_out: number;
    net_external: number;
}

export interface AttributionSummaryMonth {
    month: string;
    delta: number;
    price_effect: number;
    trade_effect: number;
    transfer_effect: number;
    income_effect: number;
    residual: number;
    /** null = month has no classified cash_flow_tags at all (render "—"), distinct from a true ¥0 */
    flows: AttributionFlows | null;
    savings_rate: number | null;
    invest_ratio: number | null;
    dq_count: number;
}

export interface AttributionSummaryResponse {
    months: AttributionSummaryMonth[];
    /**
     * Trailing-12-DATA-month contribution/savings authority, sourced from
     * investment_contributions.contributions_summary_v2 (月度收支-derived) —
     * NOT from the per-month `flows`/`invest_ratio` above (cash_flow_tags-
     * derived). Never sum the two (plan 2026-07-20-investment-contributions-
     * savings.md §Reconciliation). Optional: absent on responses from a
     * backend that predates these fields.
     *
     * TWO DIFFERENT RATES since 2026-08-01 (docs/api-specs/attribution.md,
     * plan 2026-08-01-ie-column-mapping-and-ibkr-amounts.md §WS-G) — ~19pp
     * apart on live data, so never render one under the other's caption:
     *   savings_rate_ttm    = (income_basis − expense_basis) / income_basis
     *                         — everything not spent (60.25% live)
     *   investment_rate_ttm = (net_external + rsu_retained) / income_basis
     *                         — the share that reached an investment account
     *                           (41.56% live)
     *   undeployed_cash_ttm = their difference in CNY (e.g. ¥42,000)
     * Both rates are null when the window has no income basis — render "—".
     */
    savings_rate_ttm?: number | null;
    investment_rate_ttm?: number | null;
    undeployed_cash_ttm?: number;
    income_basis_ttm?: number;
    expense_basis_ttm?: number;
    net_external_ttm?: number;
    rsu_retained_ttm?: number;
    internal_realloc_ttm?: number;
    gross_invested_ttm?: number;
    /** Excel-equivalent 总收入合计, DERIVED from leaf columns — not a denominator. */
    income_ttm?: number;
    window_start_month?: string | null;
    window_end_month?: string | null;
}

export interface AttributionRecomputeRequest {
    months: number;
}

export interface AttributionRecomputeMonthResult {
    month: string;
    row_count: number;
    dq_count: number;
}

export interface AttributionRecomputeResponse {
    months: AttributionRecomputeMonthResult[];
}

// === Forecast levers (GET /forecast/levers — R-2, docs/plans/2026-07-25-forecast-planning-redesign.md) ===
// Base case + sensitivity grid over savings/return/volatility, evaluated at the
// volatility-drag-adjusted median_return (src/services/forecast_levers.py::compute_levers).
// EVERY numeric field can be null when an input is unavailable — render an
// em-dash, never 0, never NaN (see src/services/forecast_levers.py module docstring).

/** W-3: analytic crossing-TIME percentiles — the year at which there is a
 * p% probability the portfolio's VALUE AT THAT TIME is >= target (NOT
 * first-passage time; see src/financial_analysis/projection_defaults.py::
 * crossing_time_percentiles docstring for the exact definition). p25 <
 * p50 < p75 always holds by construction — no frontend inversion needed,
 * unlike the retired percentile-of-VALUE-path approximation this replaces
 * (docs/decisions/ADR-026-median-basis-forecast-engine.md). null (never a
 * fabricated year) when unavailable or unreachable within the 60y horizon. */
export interface ForecastCrossingYears {
    p25: number | null;
    p50: number | null;
    p75: number | null;
}

/** The live, derived inputs + headline years_to_target. Never re-derive these
 * client-side — render exactly what the backend computed. */
export interface ForecastLeversBase {
    current_nw: number | null;
    expected_return: number | null;
    volatility: number | null;
    /** Volatility-drag-adjusted median return: exp(ln(1+r) - sigma^2/2) - 1 (R-1). */
    median_return: number | null;
    monthly_contribution: number | null;
    target: number | null;
    years_to_target: number | null;
    crossing_years: ForecastCrossingYears;
}

/** One row of the "Save more" lever — monthly_contribution is a lever-specific
 * step (fraction of the CURRENT run-rate), not the base contribution. */
export interface ForecastSavingsLeverRow {
    label: string;
    monthly_contribution: number | null;
    years_to_target: number | null;
    delta_years: number | null;
}

/** One row of the "Earn more" lever. */
export interface ForecastReturnLeverRow {
    label: string;
    expected_return: number | null;
    years_to_target: number | null;
    delta_years: number | null;
}

/** One row of the "Take less risk" lever. */
export interface ForecastVolatilityLeverRow {
    label: string;
    volatility: number | null;
    years_to_target: number | null;
    delta_years: number | null;
}

/** First step of all three levers applied together. */
export interface ForecastCombinedLever {
    label: string;
    years_to_target: number | null;
    delta_years: number | null;
}

/** W-2: echoes the (clamped) slider params actually used — present ONLY
 * when at least one of savings_pct/return_pp/volatility_pp was supplied on
 * the request; absent entirely on the plain (no query params) call. */
export interface ForecastLeversApplied {
    savings_pct: number | null;
    return_pp: number | null;
    volatility_pp: number | null;
}

/** W-1 (docs/plans/2026-07-26-your-path-design-implementation.md §3) — the
 * single resolved North Star target, from src.services.goal_resolver.
 * `source: "config_fallback"` means no active retirement goal exists in the
 * Goals table; the UI MUST surface a prompt to create one (never present the
 * fallback figure as the owner's own goal). */
export interface ForecastGoal {
    target_amount: number;
    source: 'goals' | 'config_fallback';
    goal_id: number | null;
    name: string | null;
    target_date: string | null;
    fallback_reason: string | null;
}

/** Response from GET /forecast/levers. */
export interface ForecastLevers {
    base: ForecastLeversBase;
    levers: {
        savings: ForecastSavingsLeverRow[];
        return: ForecastReturnLeverRow[];
        volatility: ForecastVolatilityLeverRow[];
    };
    combined: ForecastCombinedLever;
    /** W-2 — only present when the request supplied at least one slider param. */
    applied?: ForecastLeversApplied;
    goal: ForecastGoal;
}
