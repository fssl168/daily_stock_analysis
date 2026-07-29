import { test, expect } from '@playwright/test';

/**
 * Mock data for portfolio page integration tests.
 */
const mockPortfolioAccounts = {
  accounts: [
    {
      id: 1,
      owner_id: null,
      name: '实盘账户 A',
      broker: 'Demo',
      market: 'cn',
      base_currency: 'CNY',
    },
  ],
};

const mockPortfolioSnapshot = {
  as_of: '2026-07-28T10:00:00',
  cost_method: 'fifo',
  currency: 'CNY',
  account_count: 1,
  total_cash: 50000,
  total_market_value: 120000,
  total_equity: 170000,
  fx_stale: false,
  accounts: [
    {
      account_id: 1,
      account_name: '实盘账户 A',
      currency: 'CNY',
      cash: 50000,
      market_value: 120000,
      equity: 170000,
      positions: [],
    },
  ],
  data_quality: 'ok',
  limitations: [],
};

const mockPaperAccounts = {
  accounts: [
    {
      account_id: 1,
      name: 'default',
      initial_capital: 100000,
      cash: 85000.5,
      frozen_cash: 0,
      total_market_value: 14999.5,
      net_value: 100000,
      return_pct: 0,
      position_count: 1,
      status: 'active',
    },
    {
      account_id: 2,
      name: '测试策略',
      initial_capital: 50000,
      cash: 30000,
      frozen_cash: 0,
      total_market_value: 22000,
      net_value: 52000,
      return_pct: 4,
      position_count: 2,
      status: 'active',
    },
  ],
  total: 2,
};

/**
 * Fulfill a route with JSON and CORS headers.
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
 * Set up API mocks for the portfolio page.
 */
async function mockPortfolioApis(page: import('@playwright/test').Page) {
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

  // Portfolio accounts list.
  await page.route('**/api/v1/portfolio/accounts', async (route, request) => {
    if (request.method() === 'OPTIONS') {
      await fulfillOptions(route);
      return;
    }
    await fulfillJson(route, mockPortfolioAccounts);
  });

  // Portfolio snapshot.
  await page.route('**/api/v1/portfolio/snapshot*', async (route, request) => {
    if (request.method() === 'OPTIONS') {
      await fulfillOptions(route);
      return;
    }
    await fulfillJson(route, mockPortfolioSnapshot);
  });

  // Global CORS preflight handler for paper-trading endpoints.
  await page.route('**/api/v1/paper-trading/**', async (route, request) => {
    if (request.method() === 'OPTIONS') {
      await fulfillOptions(route);
      return;
    }
    await route.fallback();
  });

  // Paper trading account list.
  await page.route('**/api/v1/paper-trading/accounts', async (route, request) => {
    const url = new URL(request.url());
    if (url.pathname !== '/api/v1/paper-trading/accounts') {
      await route.fallback();
      return;
    }
    if (request.method() === 'OPTIONS') {
      await fulfillOptions(route);
      return;
    }
    if (request.method() === 'GET') {
      await fulfillJson(route, mockPaperAccounts);
      return;
    }
    await route.fallback();
  });
}

test.describe('Portfolio Page', () => {
  test.beforeEach(async ({ page }) => {
    await mockPortfolioApis(page);
    await page.goto('/portfolio');
  });

  test('displays paper trading accounts in account view', async ({ page }) => {
    await expect(page.getByTestId('paper-accounts-section')).toBeVisible();
    await expect(page.getByText('纸面账户')).toBeVisible();

    // Both mock paper accounts should render as cards.
    await expect(page.getByTestId('paper-account-card-1')).toBeVisible();
    await expect(page.getByTestId('paper-account-card-2')).toBeVisible();

    // Account 1 values.
    await expect(page.getByTestId('paper-account-card-1')).toContainText('default');
    await expect(page.getByTestId('paper-account-card-1')).toContainText('100,000.00');
    await expect(page.getByTestId('paper-account-card-1')).toContainText('运行中');

    // Account 2 values with positive return.
    await expect(page.getByTestId('paper-account-card-2')).toContainText('测试策略');
    await expect(page.getByTestId('paper-account-card-2')).toContainText('+4.00%');
  });

  test('navigates to paper trading page when card clicked', async ({ page }) => {
    await expect(page.getByTestId('paper-account-card-1')).toBeVisible();
    await page.getByTestId('paper-account-card-1').click();
    await expect(page).toHaveURL(/\/paper-trading/);
  });
});
