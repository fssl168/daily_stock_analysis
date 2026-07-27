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
  total: 1,
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
  total: 1,
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
});
