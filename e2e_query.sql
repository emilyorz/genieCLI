-- E2E test query for Trino autoresearch
-- Representative PBB-style manufacturing query with clear optimization opportunities:
-- 1. DISTINCT on full table scan (should select only needed columns)
-- 2. Subquery that could be CTE
-- 3. UNION instead of OR
-- 4. Missing partition filter

WITH base AS (
    SELECT
        t1.LR,
        t1.RECTIME,
        t1.TECH,
        t1.PART,
        t1.EQP,
        t1.EQP_GRP,
        t1.STAGE,
        t1.OPE_NO,
        t1.RECTIME AS last_rectime
    FROM memory.mfg_raw_sbb_pbb.pbb_flow_step_bt t1
    WHERE t1.TECH = 'N5'
),
eqp_map AS (
    SELECT location, eqp_grp
    FROM memory.mfg_raw_sbb_pbb.pbb_loc_eqp_grp_ut
    WHERE location IN ('FAB1', 'ETC1')
),
active_eqp AS (
    SELECT e.EQP_ID
    FROM memory.mfg_curated_sbb_pbb.pbb_pt_eqp_tt e
    WHERE e.EQP_ID LIKE 'EQP-%'
),
recipe_join AS (
    SELECT
        a.D_THESYSTEMKEY,
        a.LCRECIPE_ID,
        a1.EQP_ID AS SSET_EQP,
        CASE WHEN a1.D_THESYSTEMKEY IS NOT NULL THEN 'SSET' ELSE 'DSET' END AS recipe_type
    FROM memory.mfg_curated_siview.frlrcp a
    LEFT JOIN memory.mfg_curated_siview.frlrcp_sset a1
        ON a.D_THESYSTEMKEY = a1.D_THESYSTEMKEY
),
recipe_eqp AS (
    SELECT
        r.D_THESYSTEMKEY,
        r.LCRECIPE_ID,
        r.recipe_type,
        me.EQP_ID
    FROM recipe_join r
    LEFT JOIN memory.mfg_curated_siview.frmrcp_eqp me
        ON r.D_THESYSTEMKEY = me.D_THESYSTEMKEY
),
final AS (
    SELECT DISTINCT
        b.LR,
        b.TECH,
        b.PART,
        b.EQP,
        b.STAGE,
        b.RECTIME,
        r.LCRECIPE_ID,
        r.recipe_type,
        eq.eqp_grp
    FROM base b
    LEFT JOIN eqp_map eq ON b.EQP_GRP = eq.eqp_grp
    LEFT JOIN recipe_eqp r ON b.LR = r.D_THESYSTEMKEY
    LEFT JOIN active_eqp ae ON b.EQP = ae.EQP_ID
    WHERE b.STAGE IN ('PHOTO', 'ETCH', 'CVD')
)
SELECT
    f.LR,
    f.TECH,
    f.PART,
    f.EQP,
    f.STAGE,
    f.RECTIME,
    f.LCRECIPE_ID,
    f.recipe_type,
    f.eqp_grp,
    u.VALUE AS recipe_group
FROM final f
LEFT JOIN memory.mfg_curated_siview.frmrcp_udata u
    ON f.LCRECIPE_ID = u.D_THESYSTEMKEY AND u.NAME = 'S_RECIPE_GROUP'
WHERE f.eqp_grp IS NOT NULL
ORDER BY f.RECTIME DESC
LIMIT 200
