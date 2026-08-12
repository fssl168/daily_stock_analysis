import apiClient from './index';
import { toCamelCase } from './utils';

export interface YieldCurvePoint {
  tenor: string;
  tenorYears: number;
  yieldRate: number;
}

export interface YieldCurve {
  name: string;
  date: string | null;
  points: YieldCurvePoint[];
  source: string;
  usedFallback: boolean;
}

export interface BondDuration {
  couponRate: number;
  years: number;
  yieldRate: number;
  bondPrice: number;
  macaulayDuration: number;
  modifiedDuration: number;
  convexity: number;
}

export interface CreditSpread {
  corporateYield: number;
  treasuryYield: number;
  spreadBps: number;
  spreadPct: number;
}

export interface RepoRate {
  code: string;
  name: string;
  rate: number;
  date: string | null;
}

export const fixedIncomeApi = {
  /** China treasury yield curve. */
  getCurve: async (curveName = '中债国债收益率曲线'): Promise<YieldCurve> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/fixed-income/curve', {
      params: { curve_name: curveName },
    });
    return toCamelCase<YieldCurve>(response.data);
  },

  /** Bond duration / convexity for a fixed-coupon bond. */
  getDuration: async (coupon: number, years: number, yieldRate: number): Promise<BondDuration> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/fixed-income/duration', {
      params: { coupon, years, yield_rate: yieldRate },
    });
    return toCamelCase<BondDuration>(response.data);
  },

  /** Credit spread in bps between corporate and treasury yields. */
  getSpread: async (corporate: number, treasury: number): Promise<CreditSpread> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/fixed-income/spread', {
      params: { corporate, treasury },
    });
    return toCamelCase<CreditSpread>(response.data);
  },

  /** Money-market repo reference rates. */
  getRepoRates: async (): Promise<RepoRate[]> => {
    const response = await apiClient.get<Record<string, unknown>[]>('/api/v1/fixed-income/repo');
    return response.data.map((item) => toCamelCase<RepoRate>(item));
  },
};
