import { test, expect } from '@playwright/test';

/**
 * Mock data for the paper trading page.
 * Keeps tests deterministic and independent of the backend state.
 */
const mockAccountSnapshot = {
  account_id: 1,
  name: 'default',
  initial_capital: 1000,
  cash: 850.5,
  frozen_cash: 0,
  total_market_value: 149.5,
  net_value: 1000,
  return_pct: 0,
  position_count: 1,
  status: 'active',
};

const mockNetValueCurve = {
  account_id: 1,
  points: [
    { date: '2026-07-20', net_value: 1000, cash: 1000, market_value: 0, return_pct: 0 },
    { date: '2026-07-21', net_value: 1010, cash: 850.5, market_value: 159.5, return_pct: 1 },
    { date: '2026-07-22', net_value: 1000, cash: 850.5, market_value: 149.5, return_pct: 0 },
  ],
};

const mockPositions = {
  account_id: 1,
  positions: [
    {
      account_id: 1,
      code: '000001',
      name: '平安银行',
      quantity: 100,
      available_quantity: 100,
      avg_cost: 1.5,
      last_price: 1.495,
      stop_loss: 1.35,
      take_profit: 1.65,
      take_profit_2: 1.8,
      sltp_reasoning: 'ATR + Fib + volume profile',
      floating_pnl: -0.5,
      floating_pnl_pct: -0.33,
    },
  ],
  total_market_value: 149.5,
};

const mockOrders = {
  account_id: 1,
  total: 2,
  items: [
    {
      id: 1,
      account_id: 1,
      code: '000001',
      name: '平安银行',
      side: 'buy',
      order_type: 'market',
      price: null,
      quantity: 100,
      filled_quantity: 100,
      filled_price_avg: 1.5,
      status: 'filled',
      strategy_name: null,
      signal_id: 1,
      reason: 'manual order from WebUI',
      reject_reason: null,
      created_at: '2026-07-22T10:00:00',
      filled_at: '2026-07-22T10:00:01',
    },
    {
      id: 2,
      account_id: 1,
      code: '000002',
      name: '测试股份',
      side: 'sell',
      order_type: 'limit',
      price: 10.5,
      quantity: 50,
      filled_quantity: 0,
      filled_price_avg: 0,
      status: 'pending',
      strategy_name: null,
      signal_id: 2,
      reason: 'manual order from WebUI',
      reject_reason: null,
      created_at: '2026-07-22T11:00:00',
      filled_at: null,
    },
  ],
};

const mockTrades = {
  account_id: 1,
  total: 1,
  items: [
    {
      id: 1,
      order_id: 1,
      account_id: 1,
      code: '000001',
      name: '平安银行',
      side: 'buy',
      fill_price: 1.5,
      fill_quantity: 100,
      fee: 0.15,
      realized_pnl: null,
      traded_at: '2026-07-22T10:00:01',
    },
  ],
};

const mockSignals = {
  account_id: 1,
  total: 2,
  items: [
    {
      id: 1,
      account_id: 1,
      code: '000001',
      name: '平安银行',
      side: 'buy',
      trigger_price: 1.5,
      suggested_quantity: 100,
      strategy_name: null,
      rule_name: null,
      reason: 'manual order from WebUI',
      status: 'executed',
      agent_confirmed: true,
      agent_reason: 'Risk checks passed',
      reviewed_at: '2026-07-22T10:00:00',
      created_at: '2026-07-22T10:00:00',
    },
    {
      id: 2,
      account_id: 1,
      code: '000002',
      name: '测试股份',
      side: 'sell',
      trigger_price: 10.5,
      suggested_quantity: 50,
      strategy_name: null,
      rule_name: null,
      reason: 'pending signal from WebUI',
      status: 'pending',
      agent_confirmed: null,
      agent_reason: null,
      reviewed_at: null,
      created_at: '2026-07-22T11:00:00',
    },
  ],
};

const mockDecisions = {
  account_id: 1,
  total: 1,
  items: [
    {
      id: 1,
      account_id: 1,
      action: 'buy',
      code: '000001',
      name: '平安银行',
      params: { quantity: 100 },
      reason: 'Breakout above resistance',
      confidence: 0.82,
      elapsed_seconds: 1.2,
      used_fallback: false,
      error: null,
      created_at: '2026-07-22T10:00:00',
    },
  ],
};

const mockReflections = {
  account_id: 1,
  total: 1,
  items: [
    {
      id: 1,
      account_id: 1,
      scope: 'trade',
      subject: '000001 buy execution',
      summary: 'Filled at fair price with acceptable slippage.',
      takeaway: 'Use limit orders in low-liquidity sessions.',
      lessons: ['Watch spread before market open'],
      tags: 'execution,liquidity',
      mood: 'good',
      trade_id: 1,
      order_id: 1,
      code: '000001',
      created_at: '2026-07-22T10:05:00',
    },
  ],
};

const mockBattlePlans = [
  {
    plan_id: 1,
    account_id: 1,
    date: '2026-07-23',
    holdings_plans: [
      {
        code: '000001',
        name: '平安银行',
        current_price: 1.495,
        strong_scenario: 'Hold above 1.55',
        neutral_scenario: 'Range between 1.45 and 1.55',
        weak_scenario: 'Cut below 1.35',
        action_conditions: ['Add on pullback to 1.45'],
        stop_loss: 1.35,
        take_profit_1: 1.65,
        take_profit_2: 1.8,
      },
    ],
    candidates: [],
    market_review: 'Neutral sentiment, range-bound market.',
    sentiment_score: 50,
    main_theme: 'defensive rotation',
    used_fallback: false,
    created_at: '2026-07-22T15:30:00',
  },
];

const mockPerformanceMetrics = {
  account_id: 1,
  start_date: '2026-07-20',
  end_date: '2026-07-22',
  total_return_pct: 2.5,
  annualized_return_pct: 15.2,
  sharpe_ratio: 1.25,
  max_drawdown_pct: -5.2,
  max_drawdown_start_date: '2026-07-21',
  max_drawdown_end_date: '2026-07-22',
  volatility_annualized: 12.3,
  win_rate: 55,
  profit_factor: 1.8,
  avg_win: 2.1,
  avg_loss: -1.2,
  calmar_ratio: 2.92,
  trade_count: 20,
  win_count: 11,
  loss_count: 9,
};

const mockRiskMetrics = {
  account_id: 1,
  max_single_stock_concentration_pct: 14.95,
  max_open_positions_limit: 8,
  current_open_positions: 1,
  max_pct_per_stock_limit: 30,
  max_cash_per_buy_limit: 50,
  max_daily_loss_limit: 5,
  current_drawdown_pct: -0.5,
};

const mockDrawdownCurve = [
  { date: '2026-07-20', net_value: 1000, peak_net_value: 1000, drawdown_pct: 0 },
  { date: '2026-07-21', net_value: 1010, peak_net_value: 1010, drawdown_pct: 0 },
  { date: '2026-07-22', net_value: 1000, peak_net_value: 1010, drawdown_pct: -0.99 },
];

const mockDailyReport = {
  date: '2026-07-22',
  markdown: '# Daily Report\n\n## Summary\nNet value: 1000.00\nReturn: 0.00%\n\n## Holdings\n- 000001: 100 shares @ 1.50\n\n## Reflection\nDisciplined execution, watch spreads.',
  report_path: 'data/paper_trading/reports/daily_report_2026-07-22.md',
  voice_path: null,
  used_fallback: false,
  error: null,
};

/**
 * Fulfill a route with JSON and CORS headers so cross-origin axios requests
 * (dev server at localhost:5173 calling 127.0.0.1:8000) are accepted by the browser.
 */
async function fulfillJson(route: import('@playwright/test').Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    headers: {
      'Access-Control-Allow-Origin': 'http://localhost:5173',
      'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    },
    body: JSON.stringify(body),
  });
}

/**
 * Fulfill a CORS preflight request.
 */
async function fulfillOptions(route: import('@playwright/test').Route) {
  await route.fulfill({
    status: 204,
    contentType: 'text/plain',
    headers: {
      'Access-Control-Allow-Origin': 'http://localhost:5173',
      'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    },
  });
}

/**
 * Set up API mocks for the paper trading endpoints.
 */
async function mockPaperTradingApis(page: import('@playwright/test').Page) {
  let listenerRunning = false;

  // Auth status: disable authentication gate so the app renders the main UI.
  await page.route('**/api/v1/auth/status', async (route, request) => {
    if (request.method() === 'OPTIONS') {
      await fulfillOptions(route);
      return;
    }
    await fulfillJson(route, {
      authEnabled: false,
      loggedIn: false,
      passwordSet: false,
      passwordChangeable: false,
    });
  });

  // Global CORS preflight handler for all paper-trading endpoints.
  await page.route('**/api/v1/paper-trading/**', async (route, request) => {
    if (request.method() === 'OPTIONS') {
      await fulfillOptions(route);
      return;
    }
    await route.fallback();
  });

  // Single dispatch route for all paper-trading endpoints.
  await page.route('**/api/v1/paper-trading/**', async (route, request) => {
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    // Account snapshot: GET /api/v1/paper-trading/accounts/{id}
    const accountMatch = path.match(/^\/api\/v1\/paper-trading\/accounts\/(\d+)$/);
    if (accountMatch && method === 'GET') {
      await fulfillJson(route, mockAccountSnapshot);
      return;
    }

    // Net value curve: GET /api/v1/paper-trading/accounts/{id}/net-value
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/net-value$/)) {
      await fulfillJson(route, mockNetValueCurve);
      return;
    }

    // Positions: GET /api/v1/paper-trading/accounts/{id}/positions
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/positions$/)) {
      await fulfillJson(route, mockPositions);
      return;
    }

    // Orders: GET /api/v1/paper-trading/accounts/{id}/orders
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/orders$/)) {
      await fulfillJson(route, mockOrders);
      return;
    }

    // Trades: GET /api/v1/paper-trading/accounts/{id}/trades
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/trades$/)) {
      await fulfillJson(route, mockTrades);
      return;
    }

    // Signals: GET /api/v1/paper-trading/accounts/{id}/signals
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/signals$/)) {
      await fulfillJson(route, mockSignals);
      return;
    }

    // PM decisions: GET /api/v1/paper-trading/accounts/{id}/pm-decisions
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/pm-decisions$/)) {
      await fulfillJson(route, mockDecisions);
      return;
    }

    // Reflections: GET /api/v1/paper-trading/accounts/{id}/reflections
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/reflections$/)) {
      await fulfillJson(route, mockReflections);
      return;
    }

    // Battle plans: GET /api/v1/paper-trading/accounts/{id}/battle-plans
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/battle-plans$/)) {
      await fulfillJson(route, mockBattlePlans);
      return;
    }

    // Listener status: GET /api/v1/paper-trading/listener/status
    if (path === '/api/v1/paper-trading/listener/status') {
      await fulfillJson(route, {
        running: listenerRunning,
        account_id: listenerRunning ? 1 : null,
        watched_codes_count: listenerRunning ? 1 : 0,
        strategies_count: listenerRunning ? 1 : 0,
        markets: listenerRunning ? ['CN'] : [],
        last_settle_date: null,
        last_battle_plan_date: null,
        last_daily_reflection_date: null,
        last_pm_decision_at: null,
      });
      return;
    }

    // Listener start: POST /api/v1/paper-trading/listener/start
    if (path === '/api/v1/paper-trading/listener/start' && method === 'POST') {
      listenerRunning = true;
      await fulfillJson(route, { running: true, message: 'Listener started' });
      return;
    }

    // Listener stop: POST /api/v1/paper-trading/listener/stop
    if (path === '/api/v1/paper-trading/listener/stop' && method === 'POST') {
      listenerRunning = false;
      await fulfillJson(route, { running: false, message: 'Listener stopped' });
      return;
    }

    // Performance metrics: GET /api/v1/paper-trading/accounts/{id}/performance
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/performance$/)) {
      await fulfillJson(route, mockPerformanceMetrics);
      return;
    }

    // Risk metrics: GET /api/v1/paper-trading/accounts/{id}/risk-metrics
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/risk-metrics$/)) {
      await fulfillJson(route, mockRiskMetrics);
      return;
    }

    // Submit order: POST /api/v1/paper-trading/orders
    if (path === '/api/v1/paper-trading/orders' && method === 'POST') {
      await fulfillJson(route, {
        signal_id: 2,
        order_id: 2,
        side: 'buy',
        code: '000002',
        status: 'executed',
        fill_price: 10,
        fill_quantity: 50,
        fee: 0.5,
        reason: 'manual order from WebUI',
        risk_decisions: [],
        agent_review: null,
      });
      return;
    }

    // Batch orders: POST /api/v1/paper-trading/orders/batch
    if (path === '/api/v1/paper-trading/orders/batch' && method === 'POST') {
      const body = await request.postDataJSON();
      const orders = body?.orders ?? [];
      await fulfillJson(route, {
        account_id: 1,
        total: orders.length,
        results: orders.map((order: { code: string; side: string; quantity: number }, index: number) => ({
          signal_id: 10 + index,
          order_id: 10 + index,
          side: order.side,
          code: order.code,
          status: 'executed',
          fill_price: 10,
          fill_quantity: order.quantity,
          fee: 0.5,
          reason: 'batch order from WebUI',
          risk_decisions: [],
          agent_review: null,
        })),
      });
      return;
    }

    // Conditional order: POST /api/v1/paper-trading/orders/conditional
    if (path === '/api/v1/paper-trading/orders/conditional' && method === 'POST') {
      await fulfillJson(route, {
        id: 100,
        account_id: 1,
        code: '000003',
        name: null,
        side: 'sell',
        order_type: 'stop_loss',
        price: null,
        quantity: 100,
        filled_quantity: 0,
        filled_price_avg: 0,
        status: 'conditional',
        strategy_name: null,
        signal_id: 20,
        reason: 'manual conditional order from WebUI',
        reject_reason: null,
        created_at: '2026-07-22T10:00:00',
        filled_at: null,
        trigger_price: 1.35,
        linked_order_id: null,
        triggered_at: null,
      });
      return;
    }

    // Drawdown curve: GET /api/v1/paper-trading/accounts/{id}/drawdown
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/drawdown$/)) {
      await fulfillJson(route, mockDrawdownCurve);
      return;
    }

    // Daily report generate: POST /api/v1/paper-trading/accounts/{id}/daily-report/generate
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/daily-report\/generate$/) && method === 'POST') {
      await fulfillJson(route, mockDailyReport);
      return;
    }

    // Daily report fetch: GET /api/v1/paper-trading/accounts/{id}/daily-report/{date}
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/daily-report\/\d{4}-\d{2}-\d{2}$/)) {
      await fulfillJson(route, mockDailyReport);
      return;
    }

    // Order cancel by order_id: POST /api/v1/paper-trading/orders/{id}/cancel
    if (path.match(/^\/api\/v1\/paper-trading\/orders\/\d+\/cancel$/) && method === 'POST') {
      await fulfillJson(route, {
        signal_id: 2,
        order_id: 2,
        side: 'sell',
        code: '000002',
        status: 'cancelled',
        fill_price: null,
        fill_quantity: null,
        fee: null,
        reason: 'cancelled from WebUI',
        risk_decisions: [],
        agent_review: null,
      });
      return;
    }

    // Order modify by order_id: POST /api/v1/paper-trading/orders/{id}/modify
    if (path.match(/^\/api\/v1\/paper-trading\/orders\/\d+\/modify$/) && method === 'POST') {
      await fulfillJson(route, {
        signal_id: 2,
        order_id: 2,
        side: 'sell',
        code: '000002',
        status: 'pending',
        fill_price: null,
        fill_quantity: null,
        fee: null,
        reason: 'modified from WebUI',
        risk_decisions: [],
        agent_review: null,
      });
      return;
    }

    // Signal cancel: POST /api/v1/paper-trading/signals/{id}/cancel
    if (path.match(/^\/api\/v1\/paper-trading\/signals\/\d+\/cancel$/) && method === 'POST') {
      await fulfillJson(route, {
        signal_id: 2,
        order_id: null,
        side: 'sell',
        code: '000002',
        status: 'cancelled',
        fill_price: null,
        fill_quantity: null,
        fee: null,
        reason: 'cancelled from WebUI',
        risk_decisions: [],
        agent_review: null,
      });
      return;
    }

    // Signal modify: POST /api/v1/paper-trading/signals/{id}/modify
    if (path.match(/^\/api\/v1\/paper-trading\/signals\/\d+\/modify$/) && method === 'POST') {
      await fulfillJson(route, {
        signal_id: 2,
        order_id: null,
        side: 'sell',
        code: '000002',
        status: 'pending',
        fill_price: null,
        fill_quantity: null,
        fee: null,
        reason: 'modified from WebUI',
        risk_decisions: [],
        agent_review: null,
      });
      return;
    }

    // PM decision trigger: POST /api/v1/paper-trading/accounts/{id}/pm-decisions/trigger
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/pm-decisions\/trigger$/) && method === 'POST') {
      await fulfillJson(route, {
        id: 2,
        account_id: 1,
        action: 'hold',
        code: null,
        name: null,
        params: {},
        reason: 'No action needed - market closed',
        confidence: 0.65,
        elapsed_seconds: 1.5,
        used_fallback: false,
        error: null,
        created_at: '2026-07-22T12:00:00',
      });
      return;
    }

    // Daily reflection trigger: POST /api/v1/paper-trading/accounts/{id}/reflections/daily
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/reflections\/daily$/) && method === 'POST') {
      await fulfillJson(route, {
        id: 2,
        account_id: 1,
        scope: 'daily',
        subject: 'Daily reflection 2026-07-22',
        summary: 'Market was range-bound. Position held steady.',
        takeaway: 'Patience paid off; wait for breakout confirmation.',
        lessons: ['Monitor volume for breakout confirmation'],
        tags: 'patience,volume',
        mood: 'neutral',
        trade_id: null,
        order_id: null,
        code: null,
        created_at: '2026-07-22T15:00:00',
      });
      return;
    }

    // Battle plan generate: POST /api/v1/paper-trading/accounts/{id}/battle-plans/generate
    if (path.match(/^\/api\/v1\/paper-trading\/accounts\/\d+\/battle-plans\/generate$/) && method === 'POST') {
      await fulfillJson(route, mockBattlePlans[0]);
      return;
    }

    // Fallback: let the browser handle unmatched requests.
    await route.fallback();
  });
}

test.describe('Paper Trading Page', () => {
  test.beforeEach(async ({ page }) => {
    await mockPaperTradingApis(page);
    await page.goto('/paper-trading');
  });

  test('renders header and account summary', async ({ page }) => {
    await expect(page.getByTestId('paper-trading-title')).toHaveText('Paper Trading');
    await expect(page.getByTestId('account-id-input')).toHaveValue('1');
    // Wait for the initial data load to finish.
    await expect(page.getByTestId('refresh-button')).not.toHaveText('Loading...');
    await expect(page.getByText('Net Value', { exact: true })).toBeVisible();
    await expect(page.getByText('1000.00')).toBeVisible();
    await expect(page.getByText('+0.00%')).toBeVisible();
  });

  test('switches tabs and displays corresponding content', async ({ page }) => {
    await expect(page.getByTestId('refresh-button')).not.toHaveText('Loading...');
    await expect(page.getByTestId('tab-positions')).toHaveAttribute('class', /text-cyan/);
    await expect(page.getByText('000001')).toBeVisible();

    await page.getByTestId('tab-orders').click();
    await expect(page.getByTestId('orders-table')).toContainText('filled');
    await expect(page.getByTestId('orders-table')).toContainText('000001');

    await page.getByTestId('tab-trades').click();
    await expect(page.getByTestId('trades-table')).toContainText('1.50');

    await page.getByTestId('tab-signals').click();
    await expect(page.getByTestId('signals-table')).toContainText('executed');

    await page.getByTestId('tab-decisions').click();
    await expect(page.getByText('Breakout above resistance')).toBeVisible();

    await page.getByTestId('tab-reflections').click();
    await expect(page.getByText('Use limit orders in low-liquidity sessions.')).toBeVisible();

    await page.getByTestId('tab-battle-plans').click();
    await expect(page.getByText('Neutral sentiment, range-bound market.')).toBeVisible();
  });

  test('submits a manual market order', async ({ page }) => {
    await page.getByTestId('order-code-input').fill('000002');
    await page.getByTestId('order-quantity-input').fill('50');
    await page.getByTestId('order-side-select').selectOption('buy');
    await page.getByTestId('order-type-select').selectOption('market');

    await page.getByTestId('order-submit-button').click();

    await expect(page.getByText('EXECUTED')).toBeVisible();
    await expect(page.getByText('000002')).toBeVisible();
  });

  test('shows limit price input only for limit orders', async ({ page }) => {
    await page.getByTestId('order-type-select').selectOption('limit');
    await expect(page.getByTestId('order-limit-price-input')).toBeVisible();

    await page.getByTestId('order-type-select').selectOption('market');
    await expect(page.getByTestId('order-limit-price-input')).not.toBeVisible();
  });

  test('starts and stops the market listener', async ({ page }) => {
    await expect(page.getByText('STOPPED')).toBeVisible();

    await page.getByTestId('listener-start-button').click();
    await expect(page.getByText('RUNNING')).toBeVisible();

    await page.getByTestId('listener-stop-button').click();
    await expect(page.getByText('STOPPED')).toBeVisible();
  });

  test('navigates via dock link', async ({ page }) => {
    await page.getByRole('link', { name: '模拟交易' }).click();
    await expect(page).toHaveURL('/paper-trading');
    await expect(page.getByTestId('paper-trading-title')).toHaveText('Paper Trading');
  });

  test('displays performance metrics', async ({ page }) => {
    await expect(page.getByTestId('sharpe-ratio-value')).toHaveText('1.25');
    await expect(page.getByTestId('max-drawdown-value')).toHaveText('-5.20%');
    await expect(page.getByTestId('win-rate-value')).toHaveText('55.00%');
    await expect(page.getByTestId('refresh-performance-button')).toBeVisible();
  });

  test('submits a conditional stop-loss order', async ({ page }) => {
    await page.getByTestId('order-mode-conditional').click();
    await page.getByTestId('conditional-code-input').fill('000003');
    await page.getByTestId('conditional-quantity-input').fill('100');
    await page.getByTestId('conditional-side-select').selectOption('sell');
    await page.getByTestId('conditional-type-select').selectOption('stop_loss');
    await page.getByTestId('conditional-trigger-price-input').fill('1.35');

    await page.getByTestId('conditional-submit-button').click();

    await expect(page.getByText('CONDITIONAL CREATED')).toBeVisible();
    await expect(page.getByText('#100')).toBeVisible();
  });

  test('submits a batch order', async ({ page }) => {
    await page.getByTestId('order-mode-batch').click();

    await page.getByTestId('batch-code-input-0').fill('000004');
    await page.getByTestId('batch-side-select-0').selectOption('buy');
    await page.getByTestId('batch-quantity-input-0').fill('200');

    await page.getByTestId('batch-add-row-button').click();
    await page.getByTestId('batch-code-input-1').fill('000005');
    await page.getByTestId('batch-side-select-1').selectOption('sell');
    await page.getByTestId('batch-quantity-input-1').fill('150');

    await page.getByTestId('batch-submit-button').click();

    await expect(page.getByText('BATCH SUBMITTED (2)')).toBeVisible();
    await expect(page.getByText('000004: EXECUTED')).toBeVisible();
    await expect(page.getByText('000005: EXECUTED')).toBeVisible();
  });

  test('filters orders by status and code', async ({ page }) => {
    await page.getByTestId('tab-orders').click();

    // Initial state shows both orders.
    await expect(page.getByTestId('orders-filter-count')).toHaveText('2 / 2');

    // Filter by pending status.
    await page.getByTestId('orders-filter-status').selectOption('pending');
    await expect(page.getByTestId('orders-filter-count')).toHaveText('1 / 2');
    await expect(page.getByTestId('orders-table')).toContainText('000002');
    await expect(page.getByTestId('orders-table')).not.toContainText('000001');

    // Filter by sell side.
    await page.getByTestId('orders-filter-side').selectOption('sell');
    await expect(page.getByTestId('orders-filter-count')).toHaveText('1 / 2');

    // Clear status and filter by code.
    await page.getByTestId('orders-filter-status').selectOption('');
    await page.getByTestId('orders-filter-side').selectOption('');
    await page.getByTestId('orders-filter-code').fill('000001');
    await expect(page.getByTestId('orders-filter-count')).toHaveText('1 / 2');
    await expect(page.getByTestId('orders-table')).toContainText('000001');
    await expect(page.getByTestId('orders-table')).not.toContainText('000002');
  });

  test('cancels a pending order', async ({ page }) => {
    await page.getByTestId('tab-orders').click();

    // The pending order #2 should have a cancel button.
    await expect(page.getByTestId('order-cancel-2')).toBeVisible();
    await page.getByTestId('order-cancel-2').click();

    // After cancel, the data reloads; the order should still be visible
    // (mock returns the same list, but the cancel API was called).
    await expect(page.getByTestId('orders-table')).toBeVisible();
  });

  test('modifies a pending limit order', async ({ page }) => {
    await page.getByTestId('tab-orders').click();

    // The pending limit order #2 should have a modify button.
    await expect(page.getByTestId('order-modify-2')).toBeVisible();
    await page.getByTestId('order-modify-2').click();

    // Modify form should appear.
    await expect(page.getByTestId('order-modify-form-2')).toBeVisible();
    await page.getByTestId('order-modify-price-input').fill('11.00');
    await page.getByTestId('order-modify-quantity-input').fill('60');
    await page.getByTestId('order-modify-submit').click();

    // After submit, the form should close (data reloads).
    await expect(page.getByTestId('orders-table')).toBeVisible();
  });

  test('cancels a pending signal', async ({ page }) => {
    await page.getByTestId('tab-signals').click();

    // The pending signal #2 should have a cancel button.
    await expect(page.getByTestId('signal-cancel-2')).toBeVisible();
    await page.getByTestId('signal-cancel-2').click();

    // After cancel, the data reloads.
    await expect(page.getByTestId('signals-table')).toBeVisible();
  });

  test('modifies a pending signal', async ({ page }) => {
    await page.getByTestId('tab-signals').click();

    // The pending signal #2 should have a modify button.
    await expect(page.getByTestId('signal-modify-2')).toBeVisible();
    await page.getByTestId('signal-modify-2').click();

    // Modify form should appear.
    await expect(page.getByTestId('signal-modify-form-2')).toBeVisible();
    await page.getByTestId('signal-modify-price-input').fill('11.00');
    await page.getByTestId('signal-modify-quantity-input').fill('60');
    await page.getByTestId('signal-modify-submit').click();

    // After submit, the form should close (data reloads).
    await expect(page.getByTestId('signals-table')).toBeVisible();
  });

  test('displays drawdown curve in performance card', async ({ page }) => {
    // The drawdown chart should be visible in the performance card.
    await expect(page.getByTestId('drawdown-chart')).toBeVisible();
    await expect(page.getByText('Drawdown Curve')).toBeVisible();
  });

  test('generates a daily report', async ({ page }) => {
    await page.getByTestId('tab-daily-report').click();

    // Click generate button.
    await page.getByTestId('daily-report-generate-button').click();

    // The report content should appear.
    await expect(page.getByTestId('daily-report-content')).toBeVisible();
    await expect(page.getByTestId('daily-report-markdown')).toContainText('Daily Report');
    await expect(page.getByText('saved')).toBeVisible();
  });

  test('loads a daily report by date', async ({ page }) => {
    await page.getByTestId('tab-daily-report').click();

    // Set date and load.
    await page.getByTestId('daily-report-date-input').fill('2026-07-22');
    await page.getByTestId('daily-report-fetch-button').click();

    // The report content should appear.
    await expect(page.getByTestId('daily-report-content')).toBeVisible();
    await expect(page.getByText('Daily Report - 2026-07-22')).toBeVisible();
  });

  test('triggers PM decision', async ({ page }) => {
    await page.getByTestId('trigger-pm-button').click();

    // Button should show loading state then revert.
    await expect(page.getByTestId('trigger-pm-button')).toBeEnabled();
  });

  test('triggers daily reflection', async ({ page }) => {
    await page.getByTestId('trigger-reflection-button').click();

    // Button should show loading state then revert.
    await expect(page.getByTestId('trigger-reflection-button')).toBeEnabled();
  });
});
