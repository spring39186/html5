# Essbase Database Outline (extracted)

Source: Oracle EPM / Essbase **Outline editor** HTML (member table render).
Notation used below:

- `(+)`,`(~)` = consolidation operator: `+` Add, `~` Ignore (no consolidation).
- `[Store]` Store data · `[Label]` Label only · `[NeverShare]` Never share · `[DynCalc]` Dynamic calculation.
- `SO=n` = member solve order. `ƒ` = member has a formula (see **Formulas** appendix).
- `… (collapsed: N)` = node is collapsed in the source; N children exist but were not rendered.
- `reconstructed` = 該節點在抽出當下為收合；月/季層依實際命名 `<year>/MM`（月補零）補回，請以 cube 為準。
- `reconstructed` = 此節點在抽出當下是收合的，月份/季層依 `<year>/月` 命名慣例補回（非當次抽出，請以 cube 為準）。

---

## Dimensions (10)

| # | Dimension | Dim type | Root storage | Visible members | Notes |
|---|-----------|----------|--------------|-----------------|-------|
| 1 | Time | Time | Store data | Years 2007–2025 → H1/H2 → Q1–Q4 → months `<year>/01..12` | Attribute dims: Period, Year. **Months reconstructed** |
| 2 | Measure | Accounts | Label only | 23 | Variance / YTD analytics |
| 3 | Currency | None | Store data | NTD K, USD K | |
| 4 | Sector Total | None | Never share | 6 rollups | |
| 5 | Site Group | None | Store data | 18 | |
| 6 | Site Org | None | Store data | 10 | mostly collapsed |
| 7 | Scenario | None | Store data | 7 | |
| 8 | Filings | None | Label only | TW_Filing, US_Filing | |
| 9 | Period | Attribute (Text) | Dynamic calc | H1/H2 → Q1–Q4 → `01..12` | associated dim: **Time**. **Months reconstructed** |
| 10 | Year | Attribute (Text) | Dynamic calc | 2007–2025 | associated dim: **Time** |

---

## 1. Time  · *Time dim* · [Store]
```
Time
├─ 2007 (+) [Store]
│  ├─ 2007/H1 (+) [Store]
│  │  ├─ 2007/Q1 (+) [Store]
│  │  │  ├─ 2007/01 (+) [Store]
│  │  │  ├─ 2007/02 (+) [Store]
│  │  │  └─ 2007/03 (+) [Store]
│  │  └─ 2007/Q2 (+) [Store]
│  │     ├─ 2007/04 (+) [Store]
│  │     ├─ 2007/05 (+) [Store]
│  │     └─ 2007/06 (+) [Store]
│  └─ 2007/H2 (+) [Store]
│     ├─ 2007/Q3 (+) [Store]
│     │  ├─ 2007/07 (+) [Store]
│     │  ├─ 2007/08 (+) [Store]
│     │  └─ 2007/09 (+) [Store]
│     └─ 2007/Q4 (+) [Store]
│        ├─ 2007/10 (+) [Store]
│        ├─ 2007/11 (+) [Store]
│        └─ 2007/12 (+) [Store]
├─ 2008 (+) [Store]
│  ├─ 2008/H1 (+) [Store]
│  │  ├─ 2008/Q1 (+) [Store]
│  │  │  ├─ 2008/01 (+) [Store]
│  │  │  ├─ 2008/02 (+) [Store]
│  │  │  └─ 2008/03 (+) [Store]
│  │  └─ 2008/Q2 (+) [Store]
│  │     ├─ 2008/04 (+) [Store]
│  │     ├─ 2008/05 (+) [Store]
│  │     └─ 2008/06 (+) [Store]
│  └─ 2008/H2 (+) [Store]
│     ├─ 2008/Q3 (+) [Store]
│     │  ├─ 2008/07 (+) [Store]
│     │  ├─ 2008/08 (+) [Store]
│     │  └─ 2008/09 (+) [Store]
│     └─ 2008/Q4 (+) [Store]
│        ├─ 2008/10 (+) [Store]
│        ├─ 2008/11 (+) [Store]
│        └─ 2008/12 (+) [Store]
├─ 2009 (+) [Store]
│  ├─ 2009/H1 (+) [Store]
│  │  ├─ 2009/Q1 (+) [Store]
│  │  │  ├─ 2009/01 (+) [Store]
│  │  │  ├─ 2009/02 (+) [Store]
│  │  │  └─ 2009/03 (+) [Store]
│  │  └─ 2009/Q2 (+) [Store]
│  │     ├─ 2009/04 (+) [Store]
│  │     ├─ 2009/05 (+) [Store]
│  │     └─ 2009/06 (+) [Store]
│  └─ 2009/H2 (+) [Store]
│     ├─ 2009/Q3 (+) [Store]
│     │  ├─ 2009/07 (+) [Store]
│     │  ├─ 2009/08 (+) [Store]
│     │  └─ 2009/09 (+) [Store]
│     └─ 2009/Q4 (+) [Store]
│        ├─ 2009/10 (+) [Store]
│        ├─ 2009/11 (+) [Store]
│        └─ 2009/12 (+) [Store]
├─ 2010 (+) [Store]
│  ├─ 2010/H1 (+) [Store]
│  │  ├─ 2010/Q1 (+) [Store]
│  │  │  ├─ 2010/01 (+) [Store]
│  │  │  ├─ 2010/02 (+) [Store]
│  │  │  └─ 2010/03 (+) [Store]
│  │  └─ 2010/Q2 (+) [Store]
│  │     ├─ 2010/04 (+) [Store]
│  │     ├─ 2010/05 (+) [Store]
│  │     └─ 2010/06 (+) [Store]
│  └─ 2010/H2 (+) [Store]
│     ├─ 2010/Q3 (+) [Store]
│     │  ├─ 2010/07 (+) [Store]
│     │  ├─ 2010/08 (+) [Store]
│     │  └─ 2010/09 (+) [Store]
│     └─ 2010/Q4 (+) [Store]
│        ├─ 2010/10 (+) [Store]
│        ├─ 2010/11 (+) [Store]
│        └─ 2010/12 (+) [Store]
├─ 2011 (+) [Store]
│  ├─ 2011/H1 (+) [Store]
│  │  ├─ 2011/Q1 (+) [Store]
│  │  │  ├─ 2011/01 (+) [Store]
│  │  │  ├─ 2011/02 (+) [Store]
│  │  │  └─ 2011/03 (+) [Store]
│  │  └─ 2011/Q2 (+) [Store]
│  │     ├─ 2011/04 (+) [Store]
│  │     ├─ 2011/05 (+) [Store]
│  │     └─ 2011/06 (+) [Store]
│  └─ 2011/H2 (+) [Store]
│     ├─ 2011/Q3 (+) [Store]
│     │  ├─ 2011/07 (+) [Store]
│     │  ├─ 2011/08 (+) [Store]
│     │  └─ 2011/09 (+) [Store]
│     └─ 2011/Q4 (+) [Store]
│        ├─ 2011/10 (+) [Store]
│        ├─ 2011/11 (+) [Store]
│        └─ 2011/12 (+) [Store]
├─ 2012 (+) [Store]
│  ├─ 2012/H1 (+) [Store]
│  │  ├─ 2012/Q1 (+) [Store]
│  │  │  ├─ 2012/01 (+) [Store]
│  │  │  ├─ 2012/02 (+) [Store]
│  │  │  └─ 2012/03 (+) [Store]
│  │  └─ 2012/Q2 (+) [Store]
│  │     ├─ 2012/04 (+) [Store]
│  │     ├─ 2012/05 (+) [Store]
│  │     └─ 2012/06 (+) [Store]
│  └─ 2012/H2 (+) [Store]
│     ├─ 2012/Q3 (+) [Store]
│     │  ├─ 2012/07 (+) [Store]
│     │  ├─ 2012/08 (+) [Store]
│     │  └─ 2012/09 (+) [Store]
│     └─ 2012/Q4 (+) [Store]
│        ├─ 2012/10 (+) [Store]
│        ├─ 2012/11 (+) [Store]
│        └─ 2012/12 (+) [Store]
├─ 2013 (+) [Store]
│  ├─ 2013/H1 (+) [Store]
│  │  ├─ 2013/Q1 (+) [Store]
│  │  │  ├─ 2013/01 (+) [Store]
│  │  │  ├─ 2013/02 (+) [Store]
│  │  │  └─ 2013/03 (+) [Store]
│  │  └─ 2013/Q2 (+) [Store]
│  │     ├─ 2013/04 (+) [Store]
│  │     ├─ 2013/05 (+) [Store]
│  │     └─ 2013/06 (+) [Store]
│  └─ 2013/H2 (+) [Store]
│     ├─ 2013/Q3 (+) [Store]
│     │  ├─ 2013/07 (+) [Store]
│     │  ├─ 2013/08 (+) [Store]
│     │  └─ 2013/09 (+) [Store]
│     └─ 2013/Q4 (+) [Store]
│        ├─ 2013/10 (+) [Store]
│        ├─ 2013/11 (+) [Store]
│        └─ 2013/12 (+) [Store]
├─ 2014 (+) [Store]
│  ├─ 2014/H1 (+) [Store]
│  │  ├─ 2014/Q1 (+) [Store]
│  │  │  ├─ 2014/01 (+) [Store]
│  │  │  ├─ 2014/02 (+) [Store]
│  │  │  └─ 2014/03 (+) [Store]
│  │  └─ 2014/Q2 (+) [Store]
│  │     ├─ 2014/04 (+) [Store]
│  │     ├─ 2014/05 (+) [Store]
│  │     └─ 2014/06 (+) [Store]
│  └─ 2014/H2 (+) [Store]
│     ├─ 2014/Q3 (+) [Store]
│     │  ├─ 2014/07 (+) [Store]
│     │  ├─ 2014/08 (+) [Store]
│     │  └─ 2014/09 (+) [Store]
│     └─ 2014/Q4 (+) [Store]
│        ├─ 2014/10 (+) [Store]
│        ├─ 2014/11 (+) [Store]
│        └─ 2014/12 (+) [Store]
├─ 2015 (+) [Store]
│  ├─ 2015/H1 (+) [Store]
│  │  ├─ 2015/Q1 (+) [Store]
│  │  │  ├─ 2015/01 (+) [Store]
│  │  │  ├─ 2015/02 (+) [Store]
│  │  │  └─ 2015/03 (+) [Store]
│  │  └─ 2015/Q2 (+) [Store]
│  │     ├─ 2015/04 (+) [Store]
│  │     ├─ 2015/05 (+) [Store]
│  │     └─ 2015/06 (+) [Store]
│  └─ 2015/H2 (+) [Store]
│     ├─ 2015/Q3 (+) [Store]
│     │  ├─ 2015/07 (+) [Store]
│     │  ├─ 2015/08 (+) [Store]
│     │  └─ 2015/09 (+) [Store]
│     └─ 2015/Q4 (+) [Store]
│        ├─ 2015/10 (+) [Store]
│        ├─ 2015/11 (+) [Store]
│        └─ 2015/12 (+) [Store]
├─ 2016 (+) [Store]
│  ├─ 2016/H1 (+) [Store]
│  │  ├─ 2016/Q1 (+) [Store]
│  │  │  ├─ 2016/01 (+) [Store]
│  │  │  ├─ 2016/02 (+) [Store]
│  │  │  └─ 2016/03 (+) [Store]
│  │  └─ 2016/Q2 (+) [Store]
│  │     ├─ 2016/04 (+) [Store]
│  │     ├─ 2016/05 (+) [Store]
│  │     └─ 2016/06 (+) [Store]
│  └─ 2016/H2 (+) [Store]
│     ├─ 2016/Q3 (+) [Store]
│     │  ├─ 2016/07 (+) [Store]
│     │  ├─ 2016/08 (+) [Store]
│     │  └─ 2016/09 (+) [Store]
│     └─ 2016/Q4 (+) [Store]
│        ├─ 2016/10 (+) [Store]
│        ├─ 2016/11 (+) [Store]
│        └─ 2016/12 (+) [Store]
├─ 2017 (+) [Store]
│  ├─ 2017/H1 (+) [Store]
│  │  ├─ 2017/Q1 (+) [Store]
│  │  │  ├─ 2017/01 (+) [Store]
│  │  │  ├─ 2017/02 (+) [Store]
│  │  │  └─ 2017/03 (+) [Store]
│  │  └─ 2017/Q2 (+) [Store]
│  │     ├─ 2017/04 (+) [Store]
│  │     ├─ 2017/05 (+) [Store]
│  │     └─ 2017/06 (+) [Store]
│  └─ 2017/H2 (+) [Store]
│     ├─ 2017/Q3 (+) [Store]
│     │  ├─ 2017/07 (+) [Store]
│     │  ├─ 2017/08 (+) [Store]
│     │  └─ 2017/09 (+) [Store]
│     └─ 2017/Q4 (+) [Store]
│        ├─ 2017/10 (+) [Store]
│        ├─ 2017/11 (+) [Store]
│        └─ 2017/12 (+) [Store]
├─ 2018 (+) [Store]
│  ├─ 2018/H1 (+) [Store]
│  │  ├─ 2018/Q1 (+) [Store]
│  │  │  ├─ 2018/01 (+) [Store]
│  │  │  ├─ 2018/02 (+) [Store]
│  │  │  └─ 2018/03 (+) [Store]
│  │  └─ 2018/Q2 (+) [Store]
│  │     ├─ 2018/04 (+) [Store]
│  │     ├─ 2018/05 (+) [Store]
│  │     └─ 2018/06 (+) [Store]
│  └─ 2018/H2 (+) [Store]
│     ├─ 2018/Q3 (+) [Store]
│     │  ├─ 2018/07 (+) [Store]
│     │  ├─ 2018/08 (+) [Store]
│     │  └─ 2018/09 (+) [Store]
│     └─ 2018/Q4 (+) [Store]
│        ├─ 2018/10 (+) [Store]
│        ├─ 2018/11 (+) [Store]
│        └─ 2018/12 (+) [Store]
├─ 2019 (+) [Store]
│  ├─ 2019/H1 (+) [Store]
│  │  ├─ 2019/Q1 (+) [Store]
│  │  │  ├─ 2019/01 (+) [Store]
│  │  │  ├─ 2019/02 (+) [Store]
│  │  │  └─ 2019/03 (+) [Store]
│  │  └─ 2019/Q2 (+) [Store]
│  │     ├─ 2019/04 (+) [Store]
│  │     ├─ 2019/05 (+) [Store]
│  │     └─ 2019/06 (+) [Store]
│  └─ 2019/H2 (+) [Store]
│     ├─ 2019/Q3 (+) [Store]
│     │  ├─ 2019/07 (+) [Store]
│     │  ├─ 2019/08 (+) [Store]
│     │  └─ 2019/09 (+) [Store]
│     └─ 2019/Q4 (+) [Store]
│        ├─ 2019/10 (+) [Store]
│        ├─ 2019/11 (+) [Store]
│        └─ 2019/12 (+) [Store]
├─ 2020 (+) [Store]
│  ├─ 2020/H1 (+) [Store]
│  │  ├─ 2020/Q1 (+) [Store]
│  │  │  ├─ 2020/01 (+) [Store]
│  │  │  ├─ 2020/02 (+) [Store]
│  │  │  └─ 2020/03 (+) [Store]
│  │  └─ 2020/Q2 (+) [Store]
│  │     ├─ 2020/04 (+) [Store]
│  │     ├─ 2020/05 (+) [Store]
│  │     └─ 2020/06 (+) [Store]
│  └─ 2020/H2 (+) [Store]
│     ├─ 2020/Q3 (+) [Store]
│     │  ├─ 2020/07 (+) [Store]
│     │  ├─ 2020/08 (+) [Store]
│     │  └─ 2020/09 (+) [Store]
│     └─ 2020/Q4 (+) [Store]
│        ├─ 2020/10 (+) [Store]
│        ├─ 2020/11 (+) [Store]
│        └─ 2020/12 (+) [Store]
├─ 2021 (+) [Store]
│  ├─ 2021/H1 (+) [Store]
│  │  ├─ 2021/Q1 (+) [Store]
│  │  │  ├─ 2021/01 (+) [Store]
│  │  │  ├─ 2021/02 (+) [Store]
│  │  │  └─ 2021/03 (+) [Store]
│  │  └─ 2021/Q2 (+) [Store]
│  │     ├─ 2021/04 (+) [Store]
│  │     ├─ 2021/05 (+) [Store]
│  │     └─ 2021/06 (+) [Store]
│  └─ 2021/H2 (+) [Store]
│     ├─ 2021/Q3 (+) [Store]
│     │  ├─ 2021/07 (+) [Store]
│     │  ├─ 2021/08 (+) [Store]
│     │  └─ 2021/09 (+) [Store]
│     └─ 2021/Q4 (+) [Store]
│        ├─ 2021/10 (+) [Store]
│        ├─ 2021/11 (+) [Store]
│        └─ 2021/12 (+) [Store]
├─ 2022 (+) [Store]
│  ├─ 2022/H1 (+) [Store]
│  │  ├─ 2022/Q1 (+) [Store]
│  │  │  ├─ 2022/01 (+) [Store]
│  │  │  ├─ 2022/02 (+) [Store]
│  │  │  └─ 2022/03 (+) [Store]
│  │  └─ 2022/Q2 (+) [Store]
│  │     ├─ 2022/04 (+) [Store]
│  │     ├─ 2022/05 (+) [Store]
│  │     └─ 2022/06 (+) [Store]
│  └─ 2022/H2 (+) [Store]
│     ├─ 2022/Q3 (+) [Store]
│     │  ├─ 2022/07 (+) [Store]
│     │  ├─ 2022/08 (+) [Store]
│     │  └─ 2022/09 (+) [Store]
│     └─ 2022/Q4 (+) [Store]
│        ├─ 2022/10 (+) [Store]
│        ├─ 2022/11 (+) [Store]
│        └─ 2022/12 (+) [Store]
├─ 2023 (+) [Store]
│  ├─ 2023/H1 (+) [Store]
│  │  ├─ 2023/Q1 (+) [Store]
│  │  │  ├─ 2023/01 (+) [Store]
│  │  │  ├─ 2023/02 (+) [Store]
│  │  │  └─ 2023/03 (+) [Store]
│  │  └─ 2023/Q2 (+) [Store]
│  │     ├─ 2023/04 (+) [Store]
│  │     ├─ 2023/05 (+) [Store]
│  │     └─ 2023/06 (+) [Store]
│  └─ 2023/H2 (+) [Store]
│     ├─ 2023/Q3 (+) [Store]
│     │  ├─ 2023/07 (+) [Store]
│     │  ├─ 2023/08 (+) [Store]
│     │  └─ 2023/09 (+) [Store]
│     └─ 2023/Q4 (+) [Store]
│        ├─ 2023/10 (+) [Store]
│        ├─ 2023/11 (+) [Store]
│        └─ 2023/12 (+) [Store]
├─ 2024 (+) [Store]
│  ├─ 2024/H1 (+) [Store]
│  │  ├─ 2024/Q1 (+) [Store]
│  │  │  ├─ 2024/01 (+) [Store]
│  │  │  ├─ 2024/02 (+) [Store]
│  │  │  └─ 2024/03 (+) [Store]
│  │  └─ 2024/Q2 (+) [Store]
│  │     ├─ 2024/04 (+) [Store]
│  │     ├─ 2024/05 (+) [Store]
│  │     └─ 2024/06 (+) [Store]
│  └─ 2024/H2 (+) [Store]
│     ├─ 2024/Q3 (+) [Store]
│     │  ├─ 2024/07 (+) [Store]
│     │  ├─ 2024/08 (+) [Store]
│     │  └─ 2024/09 (+) [Store]
│     └─ 2024/Q4 (+) [Store]
│        ├─ 2024/10 (+) [Store]
│        ├─ 2024/11 (+) [Store]
│        └─ 2024/12 (+) [Store]
└─ 2025 (+) [Store]
   ├─ 2025/H1 (+) [Store]
   │  ├─ 2025/Q1 (+) [Store]
   │  │  ├─ 2025/01 (+) [Store]
   │  │  ├─ 2025/02 (+) [Store]
   │  │  └─ 2025/03 (+) [Store]
   │  └─ 2025/Q2 (+) [Store]
   │     ├─ 2025/04 (+) [Store]
   │     ├─ 2025/05 (+) [Store]
   │     └─ 2025/06 (+) [Store]
   └─ 2025/H2 (+) [Store]
      ├─ 2025/Q3 (+) [Store]
      │  ├─ 2025/07 (+) [Store]
      │  ├─ 2025/08 (+) [Store]
      │  └─ 2025/09 (+) [Store]
      └─ 2025/Q4 (+) [Store]
         ├─ 2025/10 (+) [Store]
         ├─ 2025/11 (+) [Store]
         └─ 2025/12 (+) [Store]
```

## 2. Measure  · *Accounts dim* · [Label]
```
Measure
├─ Current        (~) [Store]
├─ OP             (+) [Store]
├─ Current %      (~) [Store] SO=67 ƒ
├─ Current_D      (~) [Store] SO=66 ƒ
├─ Current YTD    (~) [Store] SO=66 ƒ
├─ Last Period    (~) [Store] SO=67 ƒ
├─ Same Period LY (~) [Store] SO=68 ƒ
├─ CS_D           (~) [Store] SO=68 ƒ
├─ Last Year YTD  (~) [Store] SO=70 ƒ
├─ CYLY_D         (~) [Store] SO=70 ƒ
├─ CL_D           (~) [Store] SO=67 ƒ
├─ C/L Variance   (~) [Store] SO=68 ƒ
├─ C/L %          (~) [Store] SO=69 ƒ
├─ C/S Variance   (~) [Store] SO=72 ƒ
├─ C/S %          (~) [Store] SO=72 ƒ
├─ CY/LY Variance (~) [Store] SO=72 ƒ
├─ CY/LY %        (~) [Store] SO=72 ƒ
├─ Draft Variance (+) [Store] SO=66 ƒ
├─ C/D Variance   (+) [Store] SO=66 ƒ
├─ C/D%           (+) [Store] SO=67 ƒ
├─ FcstV2         (+) [Store] SO=66 ƒ
├─ C/F2 Variance  (+) [Store] SO=66 ƒ
└─ C/F2 %         (+) [Store] SO=67 ƒ
```

## 3. Currency  · *None* · [Store]
```
Currency
├─ NTD K (+) [Store]
└─ USD K (+) [Store]
```

## 4. Sector Total  · *None* · [NeverShare]
```
Sector Total
├─ Assy (+) [Store]
│  ├─ AsLogic  (+) [Store]
│  └─ AsMemory (+) [Store]
├─ Test (+) [Store]
│  ├─ TeLogic  (+) [Store]
│  └─ TeMemory (+) [Store]
├─ Material (+) [Store]
│  ├─ BGA       (+) [Store]
│  ├─ FlipChip  (+) [Store]
│  └─ LeadFrame (+) [Store]
├─ EMS (+) [Store]
│  └─ EMSEMS (+) [Store]
├─ Estate (+) [Store]
│  └─ ReEstate (+) [Store]
└─ Other (+) [Store]
   └─ OtOther (+) [Store]
```

## 5. Site Group  · *None* · [Store]
```
Site Group
├─ ASEHoldCo_Group  (+) [Store]
├─ NewK_Group       (+) [Store]
├─ M_Group          (+) [Store]
├─ T_Group          (+) [Store]
├─ Oth_Manufactors  (+) [Store]
├─ Holdings         (+) [Store]
├─ Group Elim       (+) [Store]
├─ EMS_Group        (+) [Store]
├─ R_Group          (+) [Store]
├─ OtherRE_Group    (+) [Store]
├─ RealEstate       (+) [Store]
├─ Global Elim      (+) [Store]
├─ Group            (+) [Store]
├─ ASE_Group.       (+) [Store]
├─ SPIL_Group       (+) [Store]
├─ HCOther_Group    (+) [Store]
├─ HCGlobalElim     (+) [Store]
└─ Other_Semi_Group (+) [Store]
```

## 6. Site Org  · *None* · [Store] · SO=35
```
Site Org
├─ ASEHoldCo      (+) [Store]
├─ ASEInc_Group   (+) [Store] SO=30   … (collapsed: 5)
├─ SPIL_Group     (+) [Store] SO=30   … (collapsed: 2)
├─ EMS_Group      (+) [Store] SO=30   … (collapsed: 1)
├─ ASEGIS         (+) [Store]
├─ UGlobal        (+) [Store]
├─ HCOther_Group  (+) [Store]         … (collapsed: 1)
├─ HCGlobalElim   (+) [Store] SO=30   … (collapsed: 15)
├─ OtherR_Group   (~) [Store] SO=30   … (collapsed: 15)
└─ OtherH_Group   (~) [Store] SO=32   … (collapsed: 34)
```

## 7. Scenario  · *None* · [Store]
```
Scenario
├─ Actual        (+) [Store]
├─ Draft         (+) [Store]
├─ Draft & Final (~) [Store] SO=32 ƒ
├─ ForecastV1    (+) [Store]
├─ ForecastV2    (+) [Store]
├─ ForecastV3    (+) [Store]
└─ ForecastV4    (+) [Store]
```

## 8. Filings  · *None* · [Label]
```
Filings
├─ TW_Filing (~) [Store]
└─ US_Filing (~) [Store]
```

## 9. Period  · *Attribute (Text)* · [DynCalc] · assoc dim: Time
```
Period
├─ H1 [DynCalc]
│  ├─ Q1 [DynCalc]
│  │  ├─ 01 [DynCalc]
│  │  ├─ 02 [DynCalc]
│  │  └─ 03 [DynCalc]
│  └─ Q2 [DynCalc]
│     ├─ 04 [DynCalc]
│     ├─ 05 [DynCalc]
│     └─ 06 [DynCalc]
└─ H2 [DynCalc]
   ├─ Q3 [DynCalc]
   │  ├─ 07 [DynCalc]
   │  ├─ 08 [DynCalc]
   │  └─ 09 [DynCalc]
   └─ Q4 [DynCalc]
      ├─ 10 [DynCalc]
      ├─ 11 [DynCalc]
      └─ 12 [DynCalc]
```

## 10. Year  · *Attribute (Text)* · [DynCalc] · assoc dim: Time
```
Year
├─ 2007 [DynCalc]
├─ 2008 [DynCalc]
├─ 2009 [DynCalc]
├─ 2010 [DynCalc]
├─ 2011 [DynCalc]
├─ 2012 [DynCalc]
├─ 2013 [DynCalc]
├─ 2014 [DynCalc]
├─ 2015 [DynCalc]
├─ 2016 [DynCalc]
├─ 2017 [DynCalc]
├─ 2018 [DynCalc]
├─ 2019 [DynCalc]
├─ 2020 [DynCalc]
├─ 2021 [DynCalc]
├─ 2022 [DynCalc]
├─ 2023 [DynCalc]
├─ 2024 [DynCalc]
└─ 2025 [DynCalc]
```

---

# Formulas

### Measure → Current %  (SO=67)
```mdx
/*Current % @ABS ("Sector Total" ->"Current");*/

Round((([Current].Value)/(([Sector Total],[Current]))), 5)
```

### Measure → Current_D  (SO=66)
```mdx
/*"Sector Total" ->"Current";*/
Round(([Sector Total],[Current]), 5)
```

### Measure → Current YTD  (SO=66)
```mdx
/*IF (@ISLEV(Period,0))
@SUMRANGE("Current",@CURRMBRRANGE(Period, LEV, 0,, 0));
ELSEIF(@ISLEV(Period,1))
@SUMRANGE("Current",@CURRMBRRANGE(Period, LEV, 1,, 0));
ELSEIF(@ISlev(Period,2))
@SUMRANGE("Current",@CURRMBRRANGE(Period, LEV, 2,, 0));
ELSE 
@SUMRANGE("Current",@CURRMBRRANGE(Period, LEV, 3,, 0));
ENDIF*/

/*
IIF (IsLevel([Time].CurrentMember, 0)  OR IsLevel([Time].CurrentMember, 1)  
OR IsLevel([Time].CurrentMember, 2) OR IsLevel([Time].CurrentMember, 3) ,
SUM (CrossJoin ( {PeriodsToDate ([Time].Generations(2), [Time].CurrentMember)},{[Current]})) ,
missing)*/
IIF(Is(Time.CurrentMember,Time),
SUM (CrossJoin ( {PeriodsToDate ([Time].Generations(1), [Time].CurrentMember)},{[Current]})),
SUM (CrossJoin ( {PeriodsToDate ([Time].Generations(2), [Time].CurrentMember)},{[Current]})))
```

### Measure → Last Period  (SO=67)
```mdx
/*IF(@ISMBR("Jan"))
	@Priors(SKIPNONE,Dec->Current,1,@children(year));
ELSEIF(@ISMBR(@LEVMBRS(Period,0)))
	@PRIORS(SKIPNONE,Current);
ELSEIF(@ISMBR("Q1"))
	@Priors(SKIPNONE,Q4->Current,1,@children(year));
ELSEIF(@ISMBR(@LEVMBRS(Period,1)))
	@PRIORS(SKIPNONE,Current,1,@LEVMBRS(Period,1));
ELSEIF(@ISMBR("1H"))
	@Priors(SKIPNONE,"2H"->Current,1,@children(year));
ELSEIF(@ISMBR("Period"))
	@Priors(SKIPNONE,"Period"->Current,1,@children(year));
ELSE
	@PRIORS(SKIPNONE,Current,1,@LEVMBRS(Period,2));
ENDIF*/
/*
IIF (IsLevel ([Time].CurrentMember, 0) OR IsLevel ([Time].CurrentMember, 1) 
OR  IsLevel ([Time].CurrentMember, 2) OR IsLevel ([Time].CurrentMember, 3), 
([Current],Lag ([Time].CurrentMember,1)) , MISSING )*/
/*
IIF (IsLevel([Time].CurrentMember, 0) OR IsLevel([Time].CurrentMember, 1) OR IsLevel([Time].CurrentMember, 2) OR IsLevel([Time].CurrentMember, 3)  ,(Time.CurrentMember.Lag(1, LEVEL), [Current]).Value,
IIF (IS([Period].CurrentMember, [Jan]) ,([12],Year.CurrentMember.Lag(1, LEVEL), [Current]).Value,
IIF (IS([Period].CurrentMember, [Q1]) ,([Q2],Year.CurrentMember.Lag(1, LEVEL), [Current]).Value,
IIF (IS([Period].CurrentMember, [H1]) , ([H2],Year.CurrentMember.Lag(1, LEVEL), [Current]).Value,
IIF ( IsLevel ([Period].CurrentMember, 3) , (Year.CurrentMember.Lag(1, LEVEL), [Current]).Value,
(Period.CurrentMember.Lag(1, LEVEL), [Current]).Value ))
) 
)
)*/

IIF (IsLevel([Time].CurrentMember, 0) OR IsLevel([Time].CurrentMember, 1) OR IsLevel([Time].CurrentMember, 2) OR IsLevel([Time].CurrentMember, 3)  ,(Time.CurrentMember.Lag(1, LEVEL), [Current]),
MISSING
)
```

### Measure → Same Period LY  (SO=68)
```mdx
/*@PRIORS(SKIPNONE,"Current",1, @children(year));*/
/*IIF (IsLevel ([Time].CurrentMember, 0), ([Current],Lag ([Time].CurrentMember,12)) ,
IIF (IsLevel ([Time].CurrentMember, 1), ([Current],Lag ([Time].CurrentMember,4)) , 
IIF (IsLevel ([Time].CurrentMember, 2), ([Current],Lag ([Time].CurrentMember,2)) ,
IIF (IsLevel ([Time].CurrentMember, 3), ([Current],Lag ([Time].CurrentMember,1)) , MISSING ) ) ) )*/
IIF(Is(Time.CurrentMember,[Time]),
	([Current],Lag ([Year].CurrentMember,1)),
	IIF (IsLevel ([Time].CurrentMember, 0), ([Current],Lag ([Time].CurrentMember,12)) ,
	IIF (IsLevel ([Time].CurrentMember, 1), ([Current],Lag ([Time].CurrentMember,4)) , 
	IIF (IsLevel ([Time].CurrentMember, 2), ([Current],Lag ([Time].CurrentMember,2)) ,
	IIF (IsLevel ([Time].CurrentMember, 3), ([Current],Lag ([Time].CurrentMember,1)) , MISSING ) ) ) )
)
```

### Measure → CS_D  (SO=68)
```mdx
/*@ROUND ("Same Period LY",0);*/
Round([Same Period LY],5)
```

### Measure → Last Year YTD  (SO=70)
```mdx
IIF (IsLevel([Time].CurrentMember, 0)  OR IsLevel([Time].CurrentMember, 1)  
OR IsLevel([Time].CurrentMember, 2) OR IsLevel([Time].CurrentMember, 3) ,
SUM (CrossJoin ( {PeriodsToDate ([Time].Generations(2), [Time].CurrentMember)},{[Same Period LY]})) ,
missing)
```

### Measure → CYLY_D  (SO=70)
```mdx
/*@ROUND ("Last Year YTD",0);*/
Round([Last Year YTD],5)
```

### Measure → CL_D  (SO=67)
```mdx
/*@ROUND ("Last Period",0);*/
Round([Last Period],5)
```

### Measure → C/L Variance  (SO=68)
```mdx
/*@VAR ("Current","Last Period");*/
Round([Current]-[Last Period], 5)
```

### Measure → C/L %  (SO=69)
```mdx
/* Round((([Current]-[Last Period])/[Last Period]), 5) */
IIF ( [Current]=[Last Period] or [Current] = missing, missing , 
		IIF(Round(Abs ( [Last Period] ),4) < 0 , missing ,
				Round(((round([Current],4)-round([Last Period],4))/round([Last Period],4)), 5)
		)		
 )
```

### Measure → C/S Variance  (SO=72)
```mdx
/*@VAR ("Current","Same Period LY");*/
Round([Current]-[Same Period LY],5 )
```

### Measure → C/S %  (SO=72)
```mdx
/*("C/S Variance"/"CS_D")*100;*/
/* Round(([C/S Variance]/[CS_D]), 5) */
IIF(Round(Abs ( [CS_D] ),4) <= 0 or Round(Abs ( [C/S Variance]  ),4) <= 0 or [C/S Variance] = missing or [CS_D] = missing, missing ,
	Round((Round([C/S Variance],4)/Round([CS_D],4)), 5)
)
```

### Measure → CY/LY Variance  (SO=72)
```mdx
/*@VAR ("Current YTD","Last Year YTD");*/

Round([Current YTD]-[Last Year YTD], 5)
```

### Measure → CY/LY %  (SO=72)
```mdx
/*("CY/LY Variance"/"CYLY_D")*100;*/
/* Round(([CY/LY Variance]/[CYLY_D]), 5) */
IIF(Round(Abs ( [CYLY_D] ),3) <= 0 or Round(Abs ( [CY/LY Variance]  ),3) <= 0 or [CY/LY Variance] = missing, missing ,
	Round((Round([CY/LY Variance],3)/Round([CYLY_D],3)), 5)
)
```

### Measure → Draft Variance  (SO=66)
```mdx
([Scenario].[Draft],[Current])
```

### Measure → C/D Variance  (SO=66)
```mdx
Round([Current]-([Scenario].[Draft],[Current]),5)
```

### Measure → C/D%  (SO=67)
```mdx
Round((([Current]-[Draft Variance])/[Draft Variance]), 5)
```

### Measure → FcstV2  (SO=66)
```mdx
([ForecastV2],[Current])
```

### Measure → C/F2 Variance  (SO=66)
```mdx
Round([Current]-([ForecastV2],[Current]),5)
```

### Measure → C/F2 %  (SO=67)
```mdx
Round((([Current]-[FcstV2])/[FcstV2]), 5)
```

### Scenario → Draft & Final  (SO=32)
```mdx
IIF( IsLevel(Time.CurrentMember, 0), 
     IIF( IsEmpty (([ASEKH],[Assy],[Final])), [Draft], [Final] ), 
     Round(Sum (Time.CurrentMember.Children, [Draft & Final]), 5)
)
```
