Exact match के लिए मुझे TradingView pineस्क्रिप्ट code नीचे दे रहा हूं // This Pine Script® code is subject to the terms of the Mozilla Public License 2.0 at https://mozilla.org/MPL/2.0/
// © iincomesense

// This Pine Script® code is subject to the terms of the Mozilla Public License 2.0 at https://mozilla.org/MPL/2.0/
// © iincomesense; MTF / shadow / swing visual extension
// CLEAN DISPLAY REVISION: current MTF horizontal line objects, width=1.
// Start = source zone's creation/leg-out candle OPEN time. No inter-zone joins.
// STATE REVISION: Fresh = proximal not yet touched after zone creation;
// Tested = proximal touched but distal not crossed; Broken = distal crossed.
// To keep price-chart autoscale independent of ALL indicator drawings, enable
// TradingView's "Scale price chart only" in the price scale menu (manual setting).
//@version=6
indicator("Zone MTF", overlay=true, max_bars_back=5000, max_boxes_count=500, max_labels_count=500, max_lines_count=60)

// Original indicator inputs and defaults. Parity-only inputs below are NOT filters.
accountCapital = input.float(25000.0, "Account Capital")
riskPct = input.float(0.5, "Risk %")
targetRR = input.float(5.0, "Target RR")
slBufferAtr = input.float(0.1, "SL Buffer ATR")
atrPeriod = input.int(14, "ATR Period", minval=1)
volSmaPeriod = input.int(20, "Volume SMA Period", minval=1)
legOutTrMult = input.float(1.2, "Leg-Out TR Multiplier")
legOutMinTrRatio = input.float(1.0, "Leg-Out Min TR Ratio vs Leg-In")
hqLegOutTrMult = input.float(2.0, "HQ Leg-Out TR Multiplier")
hqLegInAtrMult = input.float(1.5, "HQ Leg-In ATR Multiplier")
maxBaseAtrMult = input.float(1.0, "Max Base TR ATR Multiplier")
maxWickPct = input.float(0.30, "Max Wick %")
minBaseCountInput = input.int(1, "Min Base Count")
maxBaseCountInput = input.int(3, "Max Base Count")
legInMinAtrMult = input.float(1.0, "Leg-In Min ATR Multiplier")
minClvPct = input.float(0.60, "Min CLV %")
legInToBaseSizeMult = input.float(2.0, "Leg-In to Base Size Multiplier")
legInMinBodyPct = input.float(0.60, "Leg-In Min Body %")
useImbalance = input.bool(true, "Use Imbalance")
maxImbalanceMult = input.float(1.0, "Max Imbalance vs Leg-In Mult")
relaxGapCapOvernight = input.bool(true, "Relax Gap Cap on Overnight")
genuineGapBonus = input.int(10, "Genuine Gap Score Bonus")
overnightGapBonus = input.int(15, "Overnight Gap Score Bonus")
rejectOppositeCoverPct = input.float(0.50, "Reject Opposite Cover %")
minValidScore = input.int(40, "Min Valid Score")
hqScoreThreshold = input.int(90, "HQ Score Threshold")
legOutBodyHeavyPct = input.float(0.60, "Leg-Out Body Heavy Pressure %")
testedLegOutRetracePct = input.float(1.00, "Tested Leg-Out Retrace %")
maxTestedCount = input.int(1, "Max Tested Count")

// Original parity settings: kept for compatibility; unchanged and inactive in zone validation.
legOutToLegInBodyMult = input.float(1.0, "Leg-Out to Leg-In Body Mult")
baseBoringMaxBodyPct = input.float(0.55, "Base Small-Body (Indecision) %")
eodHighBufferPct = input.float(10.0, "EOD High Buffer %")
eodLowBufferPct = input.float(10.0, "EOD Low Buffer %")
scanAfterCandleComplete = input.bool(true, "Scan After Candle Complete")
scanMonthlyOnce = input.bool(true, "Scan Monthly Once")
scanWeeklyOnce = input.bool(true, "Scan Weekly Once")
scanDailyOnce = input.bool(true, "Scan Daily Once")
enableClosingWickCheck = input.bool(true, "Use Closing-Side Wick Guard")
legInMinClosingWickPct = input.float(0.1, "Leg-In Min Closing Wick %")
enableLegOutCoverCheck = input.bool(true, "Use Leg-Out Coverage Guard")
legOutMaxCoverPct = input.float(90.0, "Leg-Out Max Cover %")
enableHQBaseColourCheck = input.bool(true, "Use Boring-Colour HQ Flag")
hqBaseColourProbabilityPct = input.float(90.0, "HQ Base Colour Probability %")
enableWhiteAreaCheck = input.bool(true, "Use White Area Check")

// Nine requested timeframes: both smaller AND larger than chart TF.
// Chart's own TF already has boxes, so it is excluded from MTF lines.
showMTF = input.bool(true, "Show other-TF proximal lines", group="1 · MTF lines")
filterDistantMTF = input.bool(true, "Hide distant MTF lines (display only)", group="1 · MTF lines")
mtfNearPct = input.float(8.0, "Show lines within % of last price", minval=0.1, maxval=100.0, step=0.5, group="1 · MTF lines")
showShadow = input.bool(true, "Shadow when prior zone behind / broken / swing broken", group="2 · Shadows")
showFlags = input.bool(true, "Show BASE BREAK / SWING labels", group="2 · Shadows")
shadowAtr = input.float(0.08, "Shadow padding × ATR", minval=0, maxval=1, step=0.01, group="2 · Shadows")
swingLeft = input.int(3, "Confirmed swing: bars on left", minval=1, maxval=20, group="3 · Swings")
swingRight = input.int(3, "Confirmed swing: bars on right", minval=1, maxval=20, group="3 · Swings")
maxStored = input.int(220, "Max chart zones (boxes ≤ 500)", minval=10, maxval=240, group="2 · Shadows")

HARD_MAX_BASE_COUNT = 3
minBaseCount = math.max(1, math.min(minBaseCountInput, maxBaseCountInput))
maxBaseCount = math.min(maxBaseCountInput, HARD_MAX_BASE_COUNT)

// Same gap-aware TR and RMA as original; always evaluated on each context's bars.
true_range() =>
    float r = high - low
    if bar_index > 0
        r := math.max(r, math.max(math.abs(high - close[1]), math.abs(low - close[1])))
    r
current_tr = true_range()
atr_val = ta.rma(current_tr, atrPeriod)
vol_sma = ta.sma(volume, volSmaPeriod)
tr(int idx) =>
    float pc = close[idx + 1]
    math.max(high[idx] - low[idx], math.max(math.abs(high[idx] - pc), math.abs(low[idx] - pc)))
is_bull(int idx) => close[idx] > open[idx]
is_bear(int idx) => open[idx] > close[idx]
wick_pct(int idx) =>
    float r = high[idx] - low[idx]
    r == 0 ? 0.0 : ((high[idx] - math.max(open[idx], close[idx])) + (math.min(open[idx], close[idx]) - low[idx])) / r
body_pct(int idx) =>
    float r = high[idx] - low[idx]
    r == 0 ? 0.0 : math.abs(close[idx] - open[idx]) / r
isOvernightGap() => dayofweek != dayofweek[1] or time - time[1] > 86400000

type Candidate
    bool valid = false
    int baseCount = 0
    float prox = na
    float dist = na
    float mid = na
    bool demand = false
    int score = 0
    string pattern = ""
    string category = ""
    float legInTR = na
    float legOutTR = na
    float outHigh = na
    float outLow = na
    bool overnight = false
    float baseHigh = na
    float baseLow = na

// One PURE candidate function reused by chart engine and EACH HTF engine.
// Validity, score, pattern, zone levels are therefore identical in both contexts.
f_candidate(int bc) =>
    Candidate c = Candidate.new()
    int li = bc + 1
    int pi = li + 1
    for once = 0 to 0
        if na(atr_val[li])
            break
        float inTR = tr(li)
        float inLow = low[li]
        float inHigh = high[li]
        float inClose = close[li]
        float inVol = volume[li]
        float inRange = inHigh - inLow
        bool inBull = is_bull(li)
        bool inBear = is_bear(li)
        if inRange == 0 or body_pct(li) < legInMinBodyPct
            break
        bool opposite = (inBull and is_bear(pi)) or (inBear and is_bull(pi))
        float prevHi = math.max(open[pi], close[pi])
        float prevLo = math.min(open[pi], close[pi])
        float overlap = math.max(0.0, math.min(prevHi, inHigh) - math.max(prevLo, inLow))
        if opposite and overlap / inRange >= rejectOppositeCoverPct
            break
        float bullClv = (inClose - inLow) / inRange
        float bearClv = (inHigh - inClose) / inRange
        bool baseOK = true
        float baseTR = 0.0
        float baseHigh = -1.0
        float baseLow = 1000000000.0
        for b = 1 to bc
            if na(atr_val[b])
                baseOK := false
                break
            float bt = tr(b)
            if bt > maxBaseAtrMult * atr_val[b]
                baseOK := false
                break
            baseTR := math.max(baseTR, bt)
            baseHigh := math.max(baseHigh, high[b])
            baseLow := math.min(baseLow, low[b])
        if not baseOK or baseTR == 0
            break
        float effectiveMult = bc == 1 ? 1.5 : legInToBaseSizeMult
        if inTR < effectiveMult * baseTR or not (inTR >= legInMinAtrMult * atr_val[li])
            break
        float outTR = tr(0)
        bool demand = is_bull(0)
        bool supply = is_bear(0)
        if not (demand or supply)
            break
        bool explosive = outTR >= legOutTrMult * atr_val
        bool wickOK = wick_pct(0) <= maxWickPct
        bool hierarchy = (outTR >= legOutMinTrRatio * inTR) and (inTR > baseTR)
        bool volumeOK = volume > inVol
        bool overnight = isOvernightGap()
        bool gap = false
        bool imbalance = true
        if useImbalance
            if demand
                gap := low > baseHigh
                imbalance := gap or (close > inHigh)
            else
                gap := high < baseLow
                imbalance := gap or (close < inLow)
        bool bodyEngulf = math.min(open, close) <= baseLow and math.max(open, close) >= baseHigh
        if bodyEngulf and not gap
            break
        bool rbr = inBull and bullClv >= minClvPct and demand
        bool dbr = inBear and bearClv >= minClvPct and demand
        bool dbd = inBear and bearClv >= minClvPct and supply
        bool rbd = inBull and bullClv >= minClvPct and supply
        if not ((rbr or dbr or dbd or rbd) and explosive and wickOK and hierarchy and volumeOK and imbalance)
            break
        int score = 0
        if bc == 1
            score += 15
        if inTR >= hqLegInAtrMult * atr_val[li]
            score += 10
        if outTR >= hqLegOutTrMult * inTR
            score += 15
        if inTR >= 2.0 * baseTR and outTR >= 2.0 * inTR
            score += 15
        if volume > vol_sma
            score += 10
        float outRange = high - low
        float bodyPos = outRange > 0 ? (demand ? (close - low) / outRange : (high - close) / outRange) : 0.0
        if demand
            if dbr
                if bodyPos >= 0.80 or body_pct(0) >= legOutBodyHeavyPct
                    score += 15
            else
                if bodyPos >= 0.80
                    score += 15
        else
            if bodyPos >= 0.80
                score += 15
        bool oppositeBase = false
        for b = 1 to bc
            if (demand and is_bear(b)) or (supply and is_bull(b))
                oppositeBase := true
                break
        if oppositeBase
            score += 10
        score += 10
        if gap
            score += genuineGapBonus
        if overnight and gap
            score += overnightGapBonus
        if score < minValidScore
            break
        c.valid := true
        c.baseCount := bc
        c.prox := demand ? baseHigh : baseLow
        c.dist := demand ? baseLow : baseHigh
        c.mid := demand ? high - testedLegOutRetracePct * (high - low) : low + testedLegOutRetracePct * (high - low)
        c.demand := demand
        c.score := score
        c.pattern := rbr ? "RBR" : dbr ? "DBR" : dbd ? "DBD" : "RBD"
        c.category := (rbr or dbd) ? "Continuation" : "Reversal"
        c.legInTR := inTR
        c.legOutTR := outTR
        c.outHigh := high
        c.outLow := low
        c.overnight := overnight
        c.baseHigh := baseHigh
        c.baseLow := baseLow
    c

type Zone
    float proxVal
    float distVal
    float slVal
    float tpVal
    bool isDemand
    bool isHQ
    int densityScore
    string patternType
    string zoneCategory
    string state
    int touchCount
    int startBarIndex
    int createdBarIndex
    int baseCount
    float legOutHigh
    float legOutLow
    float legOutMidLevel
    bool isOvernight
    float legInTR
    float legOutTR
    box zoneBox
    box shadowBox
    label infoLabel
    int brokenAt

var array<Zone> active_zones = array.new<Zone>()
// Latest CONFIRMED, still unbroken pivot. Older pivots aren't searched.
var float lastSwingHigh = na
var float lastSwingLow = na
var int lastSwingHighBar = na
var int lastSwingLowBar = na
var int highConfirmedAt = na
var int lowConfirmedAt = na
var int highBreakAt = na
var int lowBreakAt = na
float ph = ta.pivothigh(high, swingLeft, swingRight)
float pl = ta.pivotlow(low, swingLeft, swingRight)
if not na(ph)
    lastSwingHigh := ph
    lastSwingHighBar := bar_index - swingRight
    highConfirmedAt := bar_index
    highBreakAt := na
if not na(pl)
    lastSwingLow := pl
    lastSwingLowBar := bar_index - swingRight
    lowConfirmedAt := bar_index
    lowBreakAt := na
// The FIRST close beyond a confirmed pivot; base breaks are remembered for
// up to three bars until their potential leg-out creates the new zone.
// Do not erase an older pivot just because a newer one confirms later.
if not na(lastSwingHigh) and na(highBreakAt) and bar_index >= highConfirmedAt and close > lastSwingHigh
    highBreakAt := bar_index
if not na(lastSwingLow) and na(lowBreakAt) and bar_index >= lowConfirmedAt and close < lastSwingLow
    lowBreakAt := bar_index

// Chart timeframe: original score/duplicate/box/state order retained.
if bar_index >= math.max(atrPeriod, maxBaseCount + 3, 11) and not na(atr_val)
    bool found = false
    for bc = minBaseCount to maxBaseCount
        if found
            break
        Candidate c = f_candidate(bc)
        if not c.valid
            continue
        found := true  // Original sets this BEFORE duplicate check.
        bool duplicate = false
        int checked = 0
        if array.size(active_zones) > 0
            for j = array.size(active_zones) - 1 to 0
                Zone old = array.get(active_zones, j)
                if old.state == "Broken"
                    continue
                if old.isDemand == c.demand and math.abs(old.proxVal - c.prox) < atr_val * 0.25
                    duplicate := true
                    break
                checked += 1
                if checked >= 11
                    break
        if duplicate
            continue

        // "Behind" in TIME: an older active zone existed BEFORE the first
        // base bar, regardless of its direction or distance in price.
        bool behind = false
        bool baseBreaksOld = false
        if array.size(active_zones) > 0
            for j = array.size(active_zones) - 1 to 0
                Zone old = array.get(active_zones, j)
                if old.createdBarIndex < bar_index - bc
                    if old.state != "Broken"
                        behind := true
                    // Old zone must first become Broken DURING this new base;
                    // at least one base candle must CLOSE beyond old distal.
                    if old.state == "Broken" and not na(old.brokenAt) and old.brokenAt >= bar_index - bc and old.brokenAt < bar_index
                        for b = 1 to bc
                            if (old.isDemand and close[b] < old.distVal) or (not old.isDemand and close[b] > old.distVal)
                                baseBreaksOld := true
                                break
                if behind and baseBreaksOld
                    break

        // Swing must be CONFIRMED before first base; break requires CLOSE,
        // either on first base-cross bar or on this leg-out bar.
        bool baseBrokeHigh = not na(lastSwingHigh) and lastSwingHighBar < bar_index - bc and highConfirmedAt < bar_index - bc and not na(highBreakAt) and highBreakAt >= bar_index - bc and highBreakAt < bar_index
        bool outBrokeHigh = not na(lastSwingHigh) and lastSwingHighBar < bar_index - bc and highConfirmedAt < bar_index - bc and highBreakAt == bar_index
        bool baseBrokeLow = not na(lastSwingLow) and lastSwingLowBar < bar_index - bc and lowConfirmedAt < bar_index - bc and not na(lowBreakAt) and lowBreakAt >= bar_index - bc and lowBreakAt < bar_index
        bool outBrokeLow = not na(lastSwingLow) and lastSwingLowBar < bar_index - bc and lowConfirmedAt < bar_index - bc and lowBreakAt == bar_index
        bool brokeHigh = baseBrokeHigh or outBrokeHigh
        bool brokeLow = baseBrokeLow or outBrokeLow
        bool swingBreak = brokeHigh or brokeLow

        float sl = c.demand ? c.dist - slBufferAtr * atr_val : c.dist + slBufferAtr * atr_val
        float risk = math.abs(c.prox - sl)
        float tp = c.demand ? c.prox + risk * targetRR : c.prox - risk * targetRR
        color zoneColor = c.demand ? color.green : color.red
        box halo = na
        if showShadow and (behind or baseBreaksOld or swingBreak)
            color haloColor = baseBreaksOld ? color.orange : swingBreak ? color.yellow : color.gray
            int haloOpacity = baseBreaksOld ? 72 : swingBreak ? 82 : 91
            halo := box.new(left=bar_index - bc - 1, top=math.max(c.prox, c.dist) + shadowAtr * atr_val,
                 right=bar_index + 15, bottom=math.min(c.prox, c.dist) - shadowAtr * atr_val,
                 border_color=color.new(haloColor, baseBreaksOld ? 12 : 55),
                 border_width=baseBreaksOld ? 3 : swingBreak ? 2 : 1,
                 bgcolor=color.new(haloColor, haloOpacity), xloc=xloc.bar_index)
        box zBox = box.new(left=bar_index - bc - 1, top=c.prox, right=bar_index + 15, bottom=c.dist,
             border_color=zoneColor, bgcolor=color.new(zoneColor, 85), xloc=xloc.bar_index)
        label tag = na
        if showFlags and (baseBreaksOld or swingBreak)
            string msg = (baseBreaksOld ? "BASE BREAK" : "") + (baseBreaksOld and swingBreak ? " + " : "") + (swingBreak ? (brokeHigh ? "SWING HIGH" : "SWING LOW") : "")
            tag := label.new(bar_index, c.demand ? c.prox : c.dist, msg,
                 style=c.demand ? label.style_label_up : label.style_label_down,
                 color=baseBreaksOld ? color.orange : color.yellow, textcolor=color.black, size=size.tiny)
        Zone z = Zone.new(c.prox, c.dist, sl, tp, c.demand, c.score >= hqScoreThreshold, c.score,
             c.pattern, c.category, "Fresh", 0, bar_index - bc, bar_index, bc,
             c.outHigh, c.outLow, c.mid, c.overnight, c.legInTR, c.legOutTR, zBox, halo, tag, na)
        array.push(active_zones, z)
        // 2 boxes/zone worst case; keep below TradingView max 500 boxes.
        if array.size(active_zones) > maxStored
            Zone removed = array.shift(active_zones)
            box.delete(removed.zoneBox)
            if not na(removed.shadowBox)
                box.delete(removed.shadowBox)
            if not na(removed.infoLabel)
                label.delete(removed.infoLabel)

// STATE UPDATE (revised):
//   Fresh  -> proximal not yet touched after zone creation.
//   Tested -> proximal touched (on a bar AFTER the creation bar), distal not crossed.
//   Broken -> distal crossed (demand: low <= distal, supply: high >= distal).
if array.size(active_zones) > 0
    for j = array.size(active_zones) - 1 to 0
        Zone z = array.get(active_zones, j)
        if z.state != "Broken"
            bool crossedDist = z.isDemand ? low <= z.distVal : high >= z.distVal
            bool touchedProx = bar_index > z.createdBarIndex and (z.isDemand ? low <= z.proxVal : high >= z.proxVal)
            if crossedDist
                z.state := "Broken"
            else if touchedProx
                z.state := "Tested"
                z.touchCount += 1
        if z.state == "Broken"
            if na(z.brokenAt)
                z.brokenAt := bar_index
            box.set_bgcolor(z.zoneBox, color.new(color.gray, 95))
            box.set_border_color(z.zoneBox, color.new(color.gray, 80))
            if not na(z.shadowBox)
                box.set_bgcolor(z.shadowBox, color.new(color.gray, 95))
                box.set_border_color(z.shadowBox, color.new(color.gray, 85))
        else
            box.set_right(z.zoneBox, bar_index + 15)
            if not na(z.shadowBox)
                box.set_right(z.shadowBox, bar_index + 15)

// A broken pivot is retained only for maxBaseCount bars. It can be attributed
// to a valid zone whose base broke it; it cannot tag unrelated future zones.
if not na(highBreakAt) and bar_index - highBreakAt > maxBaseCount
    lastSwingHigh := na
    lastSwingHighBar := na
    highBreakAt := na
if not na(lowBreakAt) and bar_index - lowBreakAt > maxBaseCount
    lastSwingLow := na
    lastSwingLowBar := na
    lowBreakAt := na

// --------------------- MTF: SAME candidate and state rules ------------------
// In request.security this function owns a SEPARATE array for each requested TF.
// No boxes/labels in remote contexts. Return latest active DEMAND and SUPPLY
// separately; earlier concurrent zones on same side are not drawn in MTF.
type MtfZone
    float prox
    float dist
    float mid
    bool demand
    string state
    int touches
    int createdTime
    int createdIdx

f_mtf_engine() =>
    var array<MtfZone> zones = array.new<MtfZone>()
    if bar_index >= math.max(atrPeriod, maxBaseCount + 3, 11) and not na(atr_val)
        bool found = false
        for bc = minBaseCount to maxBaseCount
            if found
                break
            Candidate c = f_candidate(bc)
            if not c.valid
                continue
            found := true
            bool duplicate = false
            int checked = 0
            if array.size(zones) > 0
                for j = array.size(zones) - 1 to 0
                    MtfZone old = array.get(zones, j)
                    if old.demand == c.demand and math.abs(old.prox - c.prox) < atr_val * 0.25
                        duplicate := true
                        break
                    checked += 1
                    if checked >= 11
                        break
            if duplicate
                continue
            array.push(zones, MtfZone.new(c.prox, c.dist, c.mid, c.demand, "Fresh", 0, time, bar_index))
    if array.size(zones) > 0
        for j = array.size(zones) - 1 to 0
            MtfZone z = array.get(zones, j)
            // Same revised rules as chart engine:
            // Fresh until proximal touched (after creation bar); Tested after touch; Broken on distal cross.
            bool crossedDist = z.demand ? low <= z.dist : high >= z.dist
            bool touchedProx = bar_index > z.createdIdx and (z.demand ? low <= z.prox : high >= z.prox)
            if crossedDist
                z.state := "Broken"
            else if touchedProx
                z.state := "Tested"
                z.touches += 1
            if z.state == "Broken"
                array.remove(zones, j) // Chart version keeps grayed boxes, but skips Broken in duplicate check.
    float latestD = na
    float latestS = na
    int dTime = na
    int sTime = na
    if array.size(zones) > 0
        for j = array.size(zones) - 1 to 0
            MtfZone z = array.get(zones, j)
            if z.demand and na(latestD)
                latestD := z.prox
                dTime := z.createdTime
            if not z.demand and na(latestS)
                latestS := z.prox
                sTime := z.createdTime
            if not na(latestD) and not na(latestS)
                break
    [latestD, latestS, dTime, sTime]

// PREVIOUS CLOSED source candle only: avoids repaint on HTF and unfinished
// lower-TF intrabars. Sentinel makes inactive periods explicit with gaps_off.
// MTF shows latest active demand + supply per requested TF, not all zones.
f_mtf_confirmed() =>
    [d, s, dt, st] = f_mtf_engine()
    [na(d[1]) ? 1e100 : d[1], na(s[1]) ? 1e100 : s[1], na(dt[1]) ? 0 : dt[1], na(st[1]) ? 0 : st[1]]

// Lower TFs return intrabar arrays. Last available intrabar is used only
// for the CURRENT active line; no step plot joins unrelated price levels.
f_lower(string tf) =>
    [a, b, at, bt] = request.security_lower_tf(syminfo.tickerid, tf, f_mtf_confirmed(), calc_bars_count=5000)
    int n = array.size(a)
    float demand = n > 0 ? array.get(a, n - 1) : 1e100
    float supply = n > 0 ? array.get(b, n - 1) : 1e100
    int dTime = n > 0 ? array.get(at, n - 1) : 0
    int sTime = n > 0 ? array.get(bt, n - 1) : 0
    [demand, supply, dTime, sTime]

f_higher(string tf) =>
    request.security(syminfo.tickerid, tf, f_mtf_confirmed(),
         gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_on, calc_bars_count=5000)

f_remote(string tf) =>
    float demand = 1e100
    float supply = 1e100
    int dTime = 0
    int sTime = 0
    float targetSec = timeframe.in_seconds(tf)
    float chartSec = timeframe.in_seconds(timeframe.period)
    if showMTF and not na(chartSec) and not na(targetSec)
        if targetSec > chartSec
            [a, b, at, bt] = f_higher(tf)
            demand := a
            supply := b
            dTime := at
            sTime := bt
        else if targetSec < chartSec
            [a, b, at, bt] = f_lower(tf)
            demand := a
            supply := b
            dTime := at
            sTime := bt
    [demand == 1e100 ? na : demand, supply == 1e100 ? na : supply, dTime, sTime]

// 3, 5, 10, 15 min; 1, 2, 4, 6 hour; Daily. Own chart TF omitted.
[d3, s3, dt3, st3] = f_remote("3")
[d5, s5, dt5, st5] = f_remote("5")
[d10, s10, dt10, st10] = f_remote("10")
[d15, s15, dt15, st15] = f_remote("15")
[d60, s60, dt60, st60] = f_remote("60")
[d120, s120, dt120, st120] = f_remote("120")
[d240, s240, dt240, st240] = f_remote("240")
[d360, s360, dt360, st360] = f_remote("360")
[dD, sD, dtD, stD] = f_remote("D")

// Only 18 possible CURRENT levels. Never plot numeric MTF series: plot() joins
// unrelated zones with vertical strokes and a distant level squeezes candles.
// xloc.bar_time starts at the SOURCE leg-out candle, not at the chart's origin.
var array<line> mtfLines = array.new<line>(18)
if barstate.islast
    array<float> levels = array.from(d3, s3, d5, s5, d10, s10, d15, s15,
         d60, s60, d120, s120, d240, s240, d360, s360, dD, sD)
    array<int> starts = array.from(dt3, st3, dt5, st5, dt10, st10, dt15, st15,
         dt60, st60, dt120, st120, dt240, st240, dt360, st360, dtD, stD)
    array<color> palette = array.from(color.aqua, color.teal, color.blue, color.navy,
         color.fuchsia, color.purple, color.orange, color.maroon, color.lime,
         color.olive, color.rgb(30, 160, 140), color.rgb(10, 90, 80),
         color.rgb(160, 100, 240), color.rgb(90, 45, 130),
         color.rgb(250, 150, 110), color.rgb(160, 70, 50),
         color.rgb(35, 185, 60), color.rgb(175, 40, 30))
    for k = 0 to 17
        line oldLine = array.get(mtfLines, k)
        if not na(oldLine)
            line.delete(oldLine)
            array.set(mtfLines, k, na)
        float level = array.get(levels, k)
        int origin = array.get(starts, k)
        // The source zone can form INSIDE the current chart bar on lower TFs.
        // Thus compare its origin with chart bar END, not chart bar OPEN.
        int endTime = na(time_close) ? time + 60000 : time_close
        // Proximity is intentionally applied ONLY to visibility, not validity.
        bool nearby = not filterDistantMTF or (close != 0 and math.abs(level - close) / math.abs(close) * 100 <= mtfNearPct)
        bool visible = showMTF and not na(level) and origin > 0 and origin < endTime and nearby
        if visible
            // Stop at current chart bar's close; never draw a vertical connector
            // or extend backwards to chart origin. MTF histories are not retained.
            line newLine = line.new(x1=origin, y1=level, x2=endTime, y2=level,
                 xloc=xloc.bar_time, extend=extend.none,
                 color=color.new(array.get(palette, k), 20), width=1)
            array.set(mtfLines, k, newLine)

plot(close, "Close Price", display=display.none)
