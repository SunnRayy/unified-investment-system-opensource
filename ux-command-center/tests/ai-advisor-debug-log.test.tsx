import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { AIAdvisor } from '../pages/AIAdvisor';
import { LLMDebugLog } from '../components/ai-advisor/LLMDebugLog';
import { ReviewFlow } from '../components/ai-advisor/ReviewFlow';
import { PortfolioFilterProvider } from '../src/context/usePortfolioFilter';

const apiMocks = vi.hoisted(() => ({
  computeBehavioralMetrics: vi.fn(),
  createStrategyMemo: vi.fn(),
  createTrade: vi.fn(),
  deleteStrategyMemo: vi.fn(),
  deleteReview: vi.fn(),
  deleteTrade: vi.fn(),
  generateReviewQuestions: vi.fn(),
  generateReview: vi.fn(),
  getBriefById: vi.fn(),
  getBriefHistory: vi.fn(),
  getContextPreview: vi.fn(),
  getReviewHistory: vi.fn(),
  renderAdvisorContext: vi.fn(),
  getLatestBehavioralMetrics: vi.fn(),
  getLatestBrief: vi.fn(),
  getLatestReview: vi.fn(),
  getLLMSettings: vi.fn(),
  getReviewById: vi.fn(),
  getStrategyMemos: vi.fn(),
  listInsights: vi.fn(),
  listTrades: vi.fn(),
  generateBrief: vi.fn(),
  promoteInsight: vi.fn(),
  searchAssets: vi.fn(),
  updateReview: vi.fn(),
}));

vi.mock('../src/services/api', () => ({
  computeBehavioralMetrics: apiMocks.computeBehavioralMetrics,
  generateReviewQuestions: apiMocks.generateReviewQuestions,
  generateReview: apiMocks.generateReview,
  getBriefById: apiMocks.getBriefById,
  getBriefHistory: apiMocks.getBriefHistory,
  getContextPreview: apiMocks.getContextPreview,
  getReviewHistory: apiMocks.getReviewHistory,
  renderAdvisorContext: apiMocks.renderAdvisorContext,
  getLatestBehavioralMetrics: apiMocks.getLatestBehavioralMetrics,
  getLatestBrief: apiMocks.getLatestBrief,
  getLatestReview: apiMocks.getLatestReview,
  getLLMSettings: apiMocks.getLLMSettings,
  getReviewById: apiMocks.getReviewById,
  getStrategyMemos: apiMocks.getStrategyMemos,
  listInsights: apiMocks.listInsights,
  listTrades: apiMocks.listTrades,
  generateBrief: apiMocks.generateBrief,
  promoteInsight: apiMocks.promoteInsight,
  updateReview: apiMocks.updateReview,
  deleteReview: apiMocks.deleteReview,
  api: {
    getStrategyMemos: apiMocks.getStrategyMemos,
    createStrategyMemo: apiMocks.createStrategyMemo,
    deleteStrategyMemo: apiMocks.deleteStrategyMemo,
    listTrades: apiMocks.listTrades,
    createTrade: apiMocks.createTrade,
    deleteTrade: apiMocks.deleteTrade,
    searchAssets: apiMocks.searchAssets,
  },
}));

const reviewContent = {
  '交易汇总': { narrative: '交易汇总摘要' },
  '建议准确性': { narrative: '建议准确性摘要' },
  '组合表现': { narrative: '组合表现摘要' },
  '经验沉淀': { narrative: '经验沉淀摘要' },
  '准则更新建议': { narrative: '准则更新建议摘要' },
};

const reviewPayload = {
  id: 42,
  report_type: 'review',
  content_json: reviewContent,
  content_markdown: '# review',
  model_used: 'gemini/gemini-2.5-flash',
  created_at: '2026-03-20T08:00:00Z',
  period_start: '2026-03-01',
  period_end: '2026-03-31',
  usage: { prompt_tokens: 100, completion_tokens: 200, total_tokens: 300 },
  prompt_text: 'prompt text for review',
  raw_response_text: 'raw response text for review',
};

  beforeEach(() => {
  cleanup();
  vi.clearAllMocks();

  apiMocks.generateReviewQuestions.mockResolvedValue({
    questions: [{ id: 1, question: 'What happened?', context: 'trade context' }],
  });
  apiMocks.generateReview.mockResolvedValue(reviewPayload);
  apiMocks.getBriefById.mockResolvedValue(null);
  apiMocks.getBriefHistory.mockResolvedValue([]);
  apiMocks.getContextPreview.mockResolvedValue({});
  apiMocks.getReviewHistory.mockResolvedValue([
    {
      id: 77,
      title: '投资复盘 2026-03',
      model_used: 'gemini/gemini-2.5-flash',
      created_at: '2026-03-20T08:00:00Z',
      period_start: '2026-03-01',
      period_end: '2026-03-31',
    },
  ]);
    apiMocks.renderAdvisorContext.mockResolvedValue({
      report_type: 'brief',
      context_text: '## Previewed Context\n\n- Equity only',
      token_estimate: { total: 321 },
      warnings: [],
  });
  apiMocks.getLatestBehavioralMetrics.mockResolvedValue([]);
  apiMocks.getLatestBrief.mockResolvedValue(null);
  apiMocks.getLatestReview.mockResolvedValue({
    ...reviewPayload,
    id: 77,
    prompt_text: 'latest prompt text',
    raw_response_text: 'latest raw response text',
  });
  apiMocks.getLLMSettings.mockResolvedValue({
    primary_model: 'gemini/gemini-2.5-flash',
    fallback_models: [],
    temperature: 0.2,
    max_output_tokens: 2048,
  });
  apiMocks.getReviewById.mockResolvedValue({
    ...reviewPayload,
    id: 99,
    prompt_text: 'linked prompt text',
    raw_response_text: 'linked raw response text',
  });
  apiMocks.getStrategyMemos.mockResolvedValue({ memos: [] });
  apiMocks.listTrades.mockResolvedValue({ trades: [] });
  apiMocks.createStrategyMemo.mockResolvedValue({});
  apiMocks.deleteStrategyMemo.mockResolvedValue(undefined);
  apiMocks.updateReview.mockResolvedValue(reviewPayload);
  apiMocks.deleteReview.mockResolvedValue(undefined);
  apiMocks.createTrade.mockResolvedValue({});
  apiMocks.deleteTrade.mockResolvedValue(undefined);
  apiMocks.searchAssets.mockResolvedValue({ assets: [] });
  apiMocks.listInsights.mockResolvedValue([
    {
      id: 7,
      category: 'strategy',
      title: 'Insight with linked review',
      body: 'Body',
      tags: '',
      confidence: 0.8,
      status: 'validated',
      recurrence_count: 1,
      entity_refs: '',
      source_report_id: 99,
      created_at: '2026-03-20T08:00:00Z',
      updated_at: '2026-03-20T08:00:00Z',
    },
  ]);
});

describe('AI Advisor debug logs', () => {
  const renderAIAdvisor = () =>
    render(
      <PortfolioFilterProvider>
        <MemoryRouter>
          <AIAdvisor />
        </MemoryRouter>
      </PortfolioFilterProvider>
    );

  const clickInsightsTab = async (user: ReturnType<typeof userEvent.setup>) => {
    const tabButtons = await screen.findAllByRole('button', { name: 'Insights' });
    for (const tabButton of tabButtons) {
      await user.click(tabButton);
      try {
        await screen.findByText('Insight Library');
        return;
      } catch {
        // Try the next matching tab button if this one did not switch the view.
      }
    }
    throw new Error('Unable to open the Insights tab');
  };

  const clickLastDebugButton = async (user: ReturnType<typeof userEvent.setup>) => {
    const debugButtons = await screen.findAllByRole('button', { name: /llm debug log/i });
    await user.click(debugButtons[debugButtons.length - 1]);
  };

  it('renders the review completion debug log with prompt and raw response text', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <ReviewFlow
          contextConfig={{
            tiers: {
              identity: { enabled: false, detail: 'summary' },
              portfolio: { enabled: true, detail: 'summary' },
              market: { enabled: false, detail: 'summary' },
              strategy: { enabled: false, detail: 'summary' },
              transactions: { enabled: false, detail: 'summary', timeframe: '14d' },
            },
            include_realtime: false,
            include_non_rebalanceable: false,
          }}
        />
      </MemoryRouter>
    );

    const periodStart = (screen.getByLabelText('Period Start') as HTMLInputElement).value;
    const periodEnd = (screen.getByLabelText('Period End') as HTMLInputElement).value;

    await user.click(screen.getByRole('button', { name: /generate review questions/i }));
    const question = await screen.findByText('What happened?');
    expect(question).not.toBeNull();

    await user.type(screen.getByPlaceholderText('Type your answer here...'), 'Because the setup changed.');
    await user.click(screen.getByRole('button', { name: /preview context/i }));
    await user.click(await screen.findByRole('button', { name: /generate review/i }));

    const debugButton = await screen.findByRole('button', { name: /llm debug log/i });
    await user.click(debugButton);

    const prompt = await screen.findByText('prompt text for review');
    expect(prompt).not.toBeNull();
    expect(screen.getByText('raw response text for review')).not.toBeNull();
    expect(apiMocks.generateReview).toHaveBeenCalledWith(
      [{ question: 'What happened?', answer: 'Because the setup changed.' }],
      periodStart,
      periodEnd,
      expect.any(Object),
      '## Previewed Context\n\n- Equity only'
    );
  });

  it('loads review history and opens a saved review from the setup screen', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <ReviewFlow
          contextConfig={{
            tiers: {
              identity: { enabled: false, detail: 'summary' },
              portfolio: { enabled: true, detail: 'summary' },
              market: { enabled: false, detail: 'summary' },
              strategy: { enabled: false, detail: 'summary' },
              transactions: { enabled: false, detail: 'summary', timeframe: '14d' },
            },
            include_realtime: false,
            include_non_rebalanceable: false,
          }}
        />
      </MemoryRouter>
    );

    expect(await screen.findByText('Recent Reviews')).not.toBeNull();
    await user.click(screen.getByRole('button', { name: 'Open' }));

    expect(apiMocks.getReviewById).toHaveBeenCalledWith(77);
    await user.click(await screen.findByRole('button', { name: /expand llm debug log/i }));
    expect(await screen.findByText('linked prompt text')).not.toBeNull();
    expect(screen.getByText('linked raw response text')).not.toBeNull();
  });

  it('does not constrain the review flow to narrow max-width containers', async () => {
    const { container } = render(
      <MemoryRouter>
        <ReviewFlow
          contextConfig={{
            tiers: {
              identity: { enabled: false, detail: 'summary' },
              portfolio: { enabled: true, detail: 'summary' },
              market: { enabled: false, detail: 'summary' },
              strategy: { enabled: false, detail: 'summary' },
              transactions: { enabled: false, detail: 'summary', timeframe: '14d' },
            },
            include_realtime: false,
            include_non_rebalanceable: false,
          }}
        />
      </MemoryRouter>
    );

    expect(await screen.findByText('Review Setup')).not.toBeNull();
    expect(container.querySelector('.max-w-xl')).toBeNull();
    expect(container.querySelector('.max-w-2xl')).toBeNull();
  });

  it('allows editing a saved review from history actions', async () => {
    const user = userEvent.setup();
    apiMocks.getReviewById.mockResolvedValueOnce({
      ...reviewPayload,
      id: 77,
      title: '投资复盘 2026-03',
      prompt_text: 'linked prompt text',
      raw_response_text: 'linked raw response text',
    });
    apiMocks.updateReview.mockResolvedValueOnce({
      ...reviewPayload,
      id: 77,
      title: 'Updated Review Title',
      content_json: {
        ...reviewPayload.content_json,
        '交易汇总': { narrative: '编辑后的交易汇总' },
      },
    });

    render(
      <MemoryRouter>
        <ReviewFlow
          contextConfig={{
            tiers: {
              identity: { enabled: false, detail: 'summary' },
              portfolio: { enabled: true, detail: 'summary' },
              market: { enabled: false, detail: 'summary' },
              strategy: { enabled: false, detail: 'summary' },
              transactions: { enabled: false, detail: 'summary', timeframe: '14d' },
            },
            include_realtime: false,
            include_non_rebalanceable: false,
          }}
        />
      </MemoryRouter>
    );

    expect(await screen.findByText('Recent Reviews')).not.toBeNull();
    await user.click(screen.getByTitle('Edit review'));

    expect(apiMocks.getReviewById).toHaveBeenCalledWith(77);
    const titleInput = await screen.findByLabelText('Review Title');
    const jsonInput = screen.getByLabelText('Review JSON');

    await user.clear(titleInput);
    await user.type(titleInput, 'Updated Review Title');
    fireEvent.change(jsonInput, {
      target: {
        value: JSON.stringify({
          ...reviewPayload.content_json,
          '交易汇总': { narrative: '编辑后的交易汇总' },
        }, null, 2),
      },
    });
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    expect(apiMocks.updateReview).toHaveBeenCalledWith(77, {
      title: 'Updated Review Title',
      content_json: {
        ...reviewPayload.content_json,
        '交易汇总': { narrative: '编辑后的交易汇总' },
      },
    });
    expect(await screen.findByText('Updated Review Title')).not.toBeNull();
    expect(screen.getByText('编辑后的交易汇总')).not.toBeNull();
  });

  it('downloads a saved review from history actions', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:test'),
      revokeObjectURL: vi.fn(),
    });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(
      <MemoryRouter>
        <ReviewFlow
          contextConfig={{
            tiers: {
              identity: { enabled: false, detail: 'summary' },
              portfolio: { enabled: true, detail: 'summary' },
              market: { enabled: false, detail: 'summary' },
              strategy: { enabled: false, detail: 'summary' },
              transactions: { enabled: false, detail: 'summary', timeframe: '14d' },
            },
            include_realtime: false,
            include_non_rebalanceable: false,
          }}
        />
      </MemoryRouter>
    );

    expect(await screen.findByText('Recent Reviews')).not.toBeNull();
    await user.click(screen.getByTitle('Download review'));

    expect(apiMocks.getReviewById).toHaveBeenCalledWith(77);
    const anchor = clickSpy.mock.instances[0] as HTMLAnchorElement;
    expect(anchor.download.endsWith('.md')).toBe(true);
  });

  it('deletes a saved review from history actions', async () => {
    const user = userEvent.setup();
    window.confirm = vi.fn().mockImplementation(() => true);
    apiMocks.getReviewHistory
      .mockResolvedValueOnce([
        {
          id: 77,
          title: '投资复盘 2026-03',
          model_used: 'gemini/gemini-2.5-flash',
          created_at: '2026-03-20T08:00:00Z',
          period_start: '2026-03-01',
          period_end: '2026-03-31',
        },
      ])
      .mockResolvedValueOnce([]);

    render(
      <MemoryRouter>
        <ReviewFlow
          contextConfig={{
            tiers: {
              identity: { enabled: false, detail: 'summary' },
              portfolio: { enabled: true, detail: 'summary' },
              market: { enabled: false, detail: 'summary' },
              strategy: { enabled: false, detail: 'summary' },
              transactions: { enabled: false, detail: 'summary', timeframe: '14d' },
            },
            include_realtime: false,
            include_non_rebalanceable: false,
          }}
        />
      </MemoryRouter>
    );

    expect(await screen.findByText('投资复盘 2026-03')).not.toBeNull();
    await user.click(screen.getByTitle('Delete review'));

    expect(window.confirm).toHaveBeenCalled();
    expect(apiMocks.deleteReview).toHaveBeenCalledWith(77);
    expect(await screen.findByText('No saved reviews yet.')).not.toBeNull();
  });

  it('renders insights on the insights tab without loading a linked review debug panel', async () => {
    const user = userEvent.setup();

    renderAIAdvisor();

    await clickInsightsTab(user);

    expect(await screen.findByText('Insight with linked review')).not.toBeNull();
    expect(screen.getByText('Body')).not.toBeNull();
    expect(screen.queryByRole('button', { name: /llm debug log/i })).toBeNull();
    expect(apiMocks.getReviewById).not.toHaveBeenCalled();
  });

  it('renders insights without fetching fallback review debug data', async () => {
    const user = userEvent.setup();
    apiMocks.listInsights.mockResolvedValueOnce([
      {
        id: 8,
        category: 'risk',
        title: 'Insight without linked review',
        body: 'Body',
        tags: '',
        confidence: 0.7,
        status: 'validated',
        recurrence_count: 1,
        entity_refs: '',
        source_report_id: null,
        created_at: '2026-03-20T09:00:00Z',
        updated_at: '2026-03-20T09:00:00Z',
      },
    ]);

    renderAIAdvisor();

    await clickInsightsTab(user);

    expect(await screen.findByText('Insight without linked review')).not.toBeNull();
    expect(screen.queryByRole('button', { name: /llm debug log/i })).toBeNull();
    expect(apiMocks.getReviewById).not.toHaveBeenCalled();
    expect(apiMocks.getLatestReview).not.toHaveBeenCalled();
  });

  it('requires preview before generating a brief and forwards the reviewed context draft', async () => {
    const user = userEvent.setup();

    renderAIAdvisor();

    expect(screen.getByRole('button', { name: /generate brief/i })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /preview context/i }));

    expect(apiMocks.renderAdvisorContext).toHaveBeenCalledWith('brief', expect.objectContaining({
      include_non_rebalanceable: false,
    }));

    const previewEditor = await screen.findByDisplayValue(/previewed context/i);
    await user.clear(previewEditor);
    await user.type(previewEditor, '## Reviewed Context\n\n- Filtered portfolio');

    const generateButton = screen.getByRole('button', { name: /generate brief/i });
    expect(generateButton).toBeEnabled();
    await user.click(generateButton);

    expect(apiMocks.generateBrief).toHaveBeenCalledWith(
      expect.any(Object),
      '## Reviewed Context\n\n- Filtered portfolio'
    );
  });

  it('shows brief actions only on the brief tab', async () => {
    const user = userEvent.setup();

    renderAIAdvisor();

    expect(screen.getByRole('button', { name: /preview context/i })).not.toBeNull();
    expect(screen.getByRole('button', { name: /generate brief/i })).not.toBeNull();

    await user.click(await screen.findByRole('button', { name: 'Memos' }));

    expect(screen.queryByRole('button', { name: /preview context/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /generate brief/i })).toBeNull();
  });

  it('renders AI Advisor tabs in the memo-first workflow order', async () => {
    renderAIAdvisor();

    const tabButtons = await screen.findAllByRole('button');
    const tabLabels = tabButtons.map((button) => button.textContent?.trim());

    expect(tabLabels.indexOf('Brief')).toBeLessThan(tabLabels.indexOf('Memos'));
    expect(tabLabels.indexOf('Memos')).toBeLessThan(tabLabels.indexOf('Record Trade'));
    expect(tabLabels.indexOf('Record Trade')).toBeLessThan(tabLabels.indexOf('Review'));
    expect(tabLabels.indexOf('Review')).toBeLessThan(tabLabels.indexOf('Insights'));
  });

  it('offers extended transaction timeframe options in the context panel', async () => {
    renderAIAdvisor();

    expect(await screen.findByRole('option', { name: '6 months' })).not.toBeNull();
    expect(await screen.findByRole('option', { name: '1 year' })).not.toBeNull();
  });

  it('previews review context before generating the final review', async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter>
        <ReviewFlow
          contextConfig={{
            tiers: {
              identity: { enabled: false, detail: 'summary' },
              portfolio: { enabled: true, detail: 'summary' },
              market: { enabled: false, detail: 'summary' },
              strategy: { enabled: false, detail: 'summary' },
              transactions: { enabled: false, detail: 'summary', timeframe: '14d' },
            },
            include_realtime: false,
            include_non_rebalanceable: false,
          }}
        />
      </MemoryRouter>
    );

    const periodStart = (screen.getByLabelText('Period Start') as HTMLInputElement).value;
    const periodEnd = (screen.getByLabelText('Period End') as HTMLInputElement).value;

    await user.click(screen.getByRole('button', { name: /generate review questions/i }));
    await screen.findByText('What happened?');

    await user.type(screen.getByPlaceholderText('Type your answer here...'), 'Because the setup changed.');
    await user.click(screen.getByRole('button', { name: /preview context/i }));

    expect(apiMocks.renderAdvisorContext).toHaveBeenCalledWith('review', expect.objectContaining({
      include_non_rebalanceable: false,
    }), expect.objectContaining({
      period_start: periodStart,
      period_end: periodEnd,
      questions_answers: [
        { question: 'What happened?', answer: 'Because the setup changed.' },
      ],
    }));

    const previewEditor = await screen.findByDisplayValue(/previewed context/i);
    await user.clear(previewEditor);
    await user.type(previewEditor, '## Reviewed Review Context\n\n- Answers confirmed');
    await user.click(screen.getByRole('button', { name: /generate review/i }));

    expect(apiMocks.generateReview).toHaveBeenCalledWith(
      [{ question: 'What happened?', answer: 'Because the setup changed.' }],
      periodStart,
      periodEnd,
      expect.any(Object),
      '## Reviewed Review Context\n\n- Answers confirmed'
    );
  });

  it('uses the updated debug log expand control styling without raw icon text', async () => {
    const user = userEvent.setup();

    render(
      <LLMDebugLog
        promptText="prompt text"
        rawResponse="raw response"
        modelUsed="gemini/gemini-2.5-flash"
        tokenCount={321}
      />
    );

    const expandButton = screen.getByRole('button', { name: /expand llm debug log/i });
    expect(expandButton.querySelector('.material-symbols-outlined')).not.toBeNull();
    expect(expandButton.querySelector('.rounded-full')).not.toBeNull();

    await user.click(expandButton);

    expect(screen.getByText('prompt text')).not.toBeNull();
    expect(screen.getByRole('button', { name: /collapse llm debug log/i })).not.toBeNull();
    expect(expandButton.querySelector('.material-icons')).toBeNull();
  });
});
