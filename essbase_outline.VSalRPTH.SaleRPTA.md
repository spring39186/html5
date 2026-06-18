# Essbase Database Outline (extracted)

Source: Oracle EPM / Essbase **Outline editor** HTML (member table render).
Notation used below:

- `(+)`,`(~)` = consolidation operator: `+` Add, `~` Ignore (no consolidation).
- `[Store]` Store data · `[Label]` Label only · `[NeverShare]` Never share · `[DynCalc]` Dynamic calculation.
- `SO=n` = member solve order. `ƒ` = member has a formula (see **Formulas** appendix).
- `… (collapsed: N)` = node is collapsed in the source; N children exist but were not rendered.

---

## Dimensions (10)

| # | Dimension | Dim type | Root storage | Visible members | Notes |
|---|-----------|----------|--------------|-----------------|-------|
| 1 | Time | Time | Store data | Years 2007–2025 (each `<2>`) | Attribute dims: Period, Year. Partially expanded |
| 2 | Measure | Accounts | Label only | 23 | Variance / YTD analytics |
| 3 | Currency | None | Store data | NTD K, USD K | |
| 4 | Sector Total | None | Never share | 6 rollups | |
| 5 | Site Group | None | Store data | 18 | |
| 6 | Site Org | None | Store data | 10 | mostly collapsed |
| 7 | Scenario | None | Store data | 7 | |
| 8 | Filings | None | Label only | TW_Filing, US_Filing | |
| 9 | Period | Attribute (Text) | Dynamic calc | H1(Q1,Q2), H2 | associated dim: **Time** |
| 10 | Year | Attribute (Text) | Dynamic calc | 2007–2025 | associated dim: **Time** |

---

## 1. Time  · *Time dim* · [Store]
```
Time
├─ 2007 (+) [Store]
│  ├─ 2007/H1 (+) [Store]   … (collapsed: 2)
│  └─ 2007/H2 (+) [Store]   … (collapsed: 2)
├─ 2008 (+) [Store]   … (collapsed: 2)
├─ 2009 (+) [Store]   … (collapsed: 2)
├─ 2010 (+) [Store]   … (collapsed: 2)
├─ 2011 (+) [Store]   … (collapsed: 2)
├─ 2012 (+) [Store]   … (collapsed: 2)
├─ 2013 (+) [Store]   … (collapsed: 2)
├─ 2014 (+) [Store]   … (collapsed: 2)
├─ 2015 (+) [Store]   … (collapsed: 2)
├─ 2016 (+) [Store]   … (collapsed: 2)
├─ 2017 (+) [Store]   … (collapsed: 2)
├─ 2018 (+) [Store]   … (collapsed: 2)
├─ 2019 (+) [Store]   … (collapsed: 2)
├─ 2020 (+) [Store]   … (collapsed: 2)
├─ 2021 (+) [Store]   … (collapsed: 2)
├─ 2022 (+) [Store]   … (collapsed: 2)
├─ 2023 (+) [Store]   … (collapsed: 2)
├─ 2024 (+) [Store]   … (collapsed: 2)
└─ 2025 (+) [Store]   … (collapsed: 2)
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
│  ├─ Q1 [DynCalc]   … (collapsed: 3)
│  └─ Q2 [DynCalc]   … (collapsed: 3)
└─ H2 [DynCalc]      … (collapsed: 2)
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
