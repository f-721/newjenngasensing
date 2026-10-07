// Share only read-only state requests; commands always reach the server.
const sharedStateRequests = new Map();
const sharedStatePaths = new Set([
  '/heart_all', '/status', '/get_control_mode', '/turn', '/clients',
  '/scores', '/get_baselines', '/coop_status', '/jenga_series',
  '/get_rotation_status', '/attack_status', '/get_heart_data', '/attack_scoring'
]);

async function fetchShared(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  if (method !== 'GET' || !sharedStatePaths.has(url)) {
    sharedStateRequests.clear();
    try { return await fetch(url, options); }
    finally { sharedStateRequests.clear(); }
  }
  let entry = sharedStateRequests.get(url);
  if (!entry || (Date.now() - entry.started >= 250 && entry.complete)) {
    entry = { started: Date.now(), complete: false };
    entry.promise = fetch(url, options).then(response => {
      entry.complete = true;
      if (!response.ok && sharedStateRequests.get(url) === entry) sharedStateRequests.delete(url);
      return response;
    }).catch(error => {
      if (sharedStateRequests.get(url) === entry) sharedStateRequests.delete(url);
      throw error;
    });
    sharedStateRequests.set(url, entry);
  }
  // Each caller consumes its own response body.
  return (await entry.promise).clone();
}

const renderedMarkup = new WeakMap();
function setHTMLIfChanged(element, markup) {
  if (!element) return;
  const previous = renderedMarkup.get(element);
  if (previous && previous.markup === markup && previous.html === element.innerHTML) return;
  element.innerHTML = markup;
  renderedMarkup.set(element, { markup, html: element.innerHTML });
}

// Skip ticks while a previous refresh is still waiting for the server.
function pollWithoutOverlap(callback, delay) {
  let pending = false;
  return setInterval(async () => {
    if (pending || document.hidden) return;
    pending = true;
    try { await callback(); }
    catch (error) { console.error("定期更新の通信エラー", error); }
    finally { pending = false; }
  }, delay);
}

let intervalId = null;
const maxHeartRates = JSON.parse(localStorage.getItem("maxHeartRates") || "{}");

const MAX_POINTS = 30;

let coopTeamByWatch = {};
let displayedCurrentTurn = null;
let displayedHeartTarget = null;

let displayedStateTimes = null;

function renderStateTimes() {
  document.querySelectorAll('#rate > [data-watch-id]').forEach(card => {
    let timing = card.querySelector('.state-quota-time');
    if (!displayedStateTimes) {
      if (timing) timing.remove();
      return;
    }
    if (!timing) {
      timing = document.createElement('div');
      timing.className = 'state-quota-time';
      card.appendChild(timing);
    }
    const entry = displayedStateTimes[card.dataset.watchId];
    const seconds = ((entry?.quota_keep_ms || 0) / 1000).toFixed(1);
    const text = `ノルマ達成側の時間（このセット）：${seconds} 秒${entry?.rank ? ` ／ ${entry.rank}位` : ''}`;
    if (timing.textContent !== text) timing.textContent = text;
    timing.classList.toggle('is-leading', Boolean(entry?.rank === 1 && entry.quota_keep_ms > 0));
  });
}

function setHighlightedHeartTarget(watchId) {
  displayedHeartTarget = watchId || null;
  highlightCurrentTurn();
  const usedEl = document.getElementById('current-used');
  if (!usedEl) return;
  usedEl.classList.toggle('has-highlighted-heart-target', Boolean(watchId));
  usedEl.querySelector('.used-heart-heading')?.remove();
  if (watchId) {
    const heading = document.createElement('strong');
    heading.className = 'used-heart-heading';
    heading.textContent = `${watchId} の心拍を利用中`;
    usedEl.prepend(heading);
  }
}

function highlightCurrentTurn() {
  document.querySelectorAll('#rate > [data-watch-id]').forEach(card => {
    const active = card.dataset.watchId === displayedCurrentTurn;
    card.classList.toggle('is-current-turn', active);
    const used = card.dataset.watchId === displayedHeartTarget;
    card.classList.toggle('uses-heartbeat', used);
    const value = card.querySelector('.current-heart-value');
    if (value) {
      if (used) value.setAttribute('title', 'この心拍数を利用中');
      else value.removeAttribute('title');
    }
    const badge = card.querySelector('.current-turn-badge');
    if (badge) badge.hidden = !active;

  });
}

function startFetching() {
  if (intervalId !== null) return;
  intervalId = pollWithoutOverlap(async () => {
    await Promise.all([fetchHeartRate(), refreshScores()]);
  }, 1000);
  document.getElementById('status').innerText = '状態: 取得中';
  localStorage.setItem("fetchingStatus", "running");
}

function stopFetching() {
  if (intervalId !== null) {
    clearInterval(intervalId);
    intervalId = null;
  }
  document.getElementById('status').innerText = '状態: 停止';
  localStorage.setItem("fetchingStatus", "stopped");
}

async function fetchHeartRate() {
  try {
    const res = await fetchShared('/heart_all');
    const text = await res.text();
    let data = {};
    try {
      data = JSON.parse(text);
    } catch (e) {
      console.error('JSON parse error:', e);
      document.getElementById('rate').innerText = '取得エラー (形式不正)';
      return;
    }

    const rateContainer = document.getElementById('rate');
    const maxContainer = document.getElementById('max-rate');
    const renderKey = JSON.stringify([
      Object.entries(data || {}).map(([id, record]) => [id, record.heartbeat]),
      coopTeamByWatch, maxHeartRates
    ]);
    if (rateContainer.dataset.renderKey === renderKey &&
        rateContainer.childElementCount === Object.keys(data || {}).length &&
        Object.keys(data || {}).length > 0) {
      highlightCurrentTurn();
      renderStateTimes();
      return;
    }
    rateContainer.innerHTML = '';
    if (maxContainer) maxContainer.innerHTML = '';

    if (!data || Object.keys(data).length === 0) {
      rateContainer.innerText = 'データがありません';
      if (maxContainer) maxContainer.innerText = '最大心拍数を記録できません';
    } else {
      for (const [device_id, record] of Object.entries(data)) {
        const bpm = record.heartbeat;
        const div = document.createElement('div');
        div.dataset.watchId = device_id;
        const bpmText = (bpm !== undefined && bpm !== null) ? `${bpm}` : "--";
        if (rateContainer.closest('.heart-display-expanded')) {
          div.className = 'current-heart-reading';
          const teamId = coopTeamByWatch[device_id];
          if (teamId) div.classList.add(`coop-${teamId.replace('_', '-')}`);
          const device = document.createElement('span');
          device.className = 'current-heart-device';
          device.innerText = device_id;
          const value = document.createElement('strong');
          value.className = 'current-heart-value';
          value.innerText = bpmText;
          const unit = document.createElement('span');
          unit.className = 'current-heart-unit';
          unit.innerText = 'bpm';
          const badge = document.createElement('span');
          badge.className = 'current-turn-badge';
          badge.innerText = '現在の手番';
          badge.hidden = device_id !== displayedCurrentTurn;
          div.append(device, value, unit, badge);
        } else {
          div.innerText = `心拍数: ${bpmText} bpm (${device_id})`;
        }
        div.style.fontSize = '1.5em';
        div.style.fontWeight = 'bold';
        rateContainer.appendChild(div);

        if (bpm !== undefined && bpm !== null) {
          if (!maxHeartRates[device_id] || bpm > maxHeartRates[device_id]) {
            maxHeartRates[device_id] = bpm;
            localStorage.setItem("maxHeartRates", JSON.stringify(maxHeartRates));
          }
        }
      }

      rateContainer.dataset.renderKey = JSON.stringify([
        Object.entries(data).map(([id, record]) => [id, record.heartbeat]),
        coopTeamByWatch, maxHeartRates
      ]);
      highlightCurrentTurn();
      renderStateTimes();

      for (const [device_id, maxBpm] of Object.entries(maxHeartRates)) {
        const div = document.createElement('div');
        div.innerText = `最大心拍数: ${maxBpm} bpm (${device_id})`;
        div.style.fontSize = '1.5em';
        div.style.fontWeight = 'bold';
        if (maxContainer) maxContainer.appendChild(div);
      }
    }
  } catch (error) {
    document.getElementById('rate').innerText = '取得エラー';
    console.error('取得中にエラーが発生しました:', error);
  }
}

async function refreshGameStatus() {
  try {
    const res = await fetchShared('/status', { cache: "no-store" });
    const data = await res.json();
    document.getElementById('game-status').innerText = 'ゲーム状態: ' + (data.running ? '開始中' : '終了');
    await updateModeButtons(data.running);
  } catch (error) {
    console.error(error);
    document.getElementById('game-status').innerText = 'ゲーム状態： 取得失敗';
  }
}

async function startGame() {
  try {
    const modeRes = await fetchShared("/get_control_mode");
    const modeData = await modeRes.json();
    if (modeData.mode === "manual_test") {
      alert("手動テストモード中はゲームを開始できません。通常モードに戻してから開始してください");
      return;
    }

    const totalSets = document.getElementById("jengaSetCount").value;
    const res = await fetchShared(`/start?mode=jenga&sets=${encodeURIComponent(totalSets)}`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      setGamePhaseBanner('ゲーム開始', 'start');
      setTimeout(() => setGamePhaseBanner('開始中', 'ready'), 1200);
      alert("ゲームを開始しました");
      isGameRunning = true;
      refreshGameStatus();
      refreshJengaSeries();
      refreshScores();
      setupGraphs();
      startPlotting();
      const mode = modeData.mode || 'self_fast';
      if (mode === 'attack_challenge_wait') {
        setTimeout(() => runTurnCountdown(5), 200);
      }
    } else {
      alert(data.message || "ゲーム開始失敗");
    }
  } catch (e) {
    console.error(e);
    alert("ゲーム開始通信エラー");
  }
}

async function nextJengaGame() {
  const button = document.getElementById("nextGameBtn");
  button.disabled = true;
  let gameSwitched = false;
  try {
    const statusRes = await fetchShared("/status", { cache: "no-store" });
    const status = await statusRes.json();
    if (status.running) {
      alert("ゲーム中です。先に「終了」ボタンでゲームを終了してください");
      return;
    }

    if (!confirm("現在の得点を維持して、次のゲームへ進みますか？")) return;

    const res = await fetchShared("/next_jenga_game", { method: "POST" });
    const responseText = await res.text();
    let data = {};
    try {
      data = responseText ? JSON.parse(responseText) : {};
    } catch {
      data = { message: responseText };
    }
    if (!res.ok) {
      const fallback = res.status === 404
        ? "サーバーへ新しいプログラムが反映されていません。サーバーを再起動してください"
        : `次のゲームへの切り替えに失敗しました（${res.status}）`;
      alert(data.message || fallback);
      return;
    }

    gameSwitched = true;
    isGameRunning = true;
    Object.keys(maxHeartRates).forEach(watchId => delete maxHeartRates[watchId]);
    localStorage.removeItem("maxHeartRates");
    document.getElementById("csvBtn").style.display = "none";
    await refreshGameStatus();
    await refreshScores();
    await refreshJengaSeries();
    await refreshCurrentTurn();
    await setupGraphs();
    startPlotting();
    alert(`SET ${data.game_number} を開始しました（累計得点は維持されています）`);
  } catch (e) {
    console.error(e);
    alert(gameSwitched
      ? "次のゲームは開始しましたが、画面の更新に失敗しました。画面を再読み込みしてください"
      : "次のゲームへの切り替えで通信エラーが発生しました");
  } finally {
    button.disabled = false;
  }
}

function getSetScoringModeLabel(mode) {
  const modeLabels = {
    success: "妨害成功型",
    impact: "影響度型",
    state: "状態管理型",
    mvp: "MVP型"
  };
  return modeLabels[mode] || "未設定";
}

const attackChallengeModes = new Set(["attack_challenge", "attack_challenge_wait"]);
let currentControlMode = null;
let latestJengaSeries = null;

function renderSetStatus() {
  if (!latestJengaSeries) return;
  const target = document.getElementById("game-number");
  if (!target) return;
  const prefix = `SET ${latestJengaSeries.game_number || 1} / ${latestJengaSeries.total_sets || 3}`;
  target.innerText = attackChallengeModes.has(currentControlMode)
    ? `${prefix}　得点方式: ${getSetScoringModeLabel(latestJengaSeries.scoring_mode || "success")}`
    : prefix;
}

function updateAttackScoringVisibility() {
  const controls = document.getElementById("attackScoringControls");
  const isAttackChallenge = attackChallengeModes.has(currentControlMode);
  if (controls) controls.style.display = isAttackChallenge ? "block" : "none";
  document.querySelectorAll(".interference-ranking").forEach(card => {
    card.hidden = !isAttackChallenge;
  });
}

function updateSetScoreDisplay(mode = null) {
  if (latestJengaSeries && mode) latestJengaSeries.scoring_mode = mode;
  renderSetStatus();
}

function escapeResultText(value) {
  return String(value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
}

function renderResultWatchLabel(watchId) {
  const match = String(watchId).match(/^watch(\d+)$/i);
  if (!match) return escapeResultText(watchId);
  return `<span class="result-watch-label"><span class="result-watch-prefix">watch</span><strong class="result-watch-number">${match[1]}</strong></span>`;
}

function renderRankingCard(title, ranking, metric, final = false, interference = false) {
  return `<section class="game-score-history-item rank-row${final ? ' final-ranking' : ''}${interference ? ' interference-ranking' : ''}">
    <h3 class="result-heading">${title}</h3>
    <ol class="result-ranking-list">${ranking.map(item => `
      <li class="result-player-row${Number(item.rank) === 1 ? ' first-place' : ''}">
        <span class="result-rank">${escapeResultText(item.rank)}<small>位</small></span>
        <span class="result-player-name">${renderResultWatchLabel(item.watch_id)}</span>
        <strong class="result-value">${escapeResultText(metric(item))}</strong>
      </li>`).join('')}</ol>
  </section>`;
}

function renderFinalResults(ranking, teams = null) {
  const tied = ranking.filter(item => Number(item.rank) === 1).length > 1;
  return `<section class="final-results" aria-label="確定した最終順位と合計得点">
    <h2>ゲーム終了・最終結果</h2>
    <div class="final-results-columns"><span>順位</span><span>${teams ? 'チーム / 所属Watch' : 'プレイヤー'}</span><span>合計得点</span></div>
    <ol class="final-results-list">${ranking.map(item => {
      const name = teams
        ? `<strong>${item.watch_id === 'team_a' ? 'チームA' : 'チームB'}</strong><small>${(teams[item.watch_id] || []).map(escapeResultText).join('・')}</small>`
        : renderResultWatchLabel(item.watch_id);
      return `<li class="final-results-row${Number(item.rank) === 1 ? ' winner' : ''}">
        <span class="final-results-rank">${escapeResultText(item.rank)}<small>位${tied && Number(item.rank) === 1 ? '（同率）' : ''}</small></span>
        <span class="final-results-player">${name}</span>
        <strong class="final-results-score">${escapeResultText(item.total_score)}<small>点</small></strong>
      </li>`;
    }).join('')}</ol>
  </section>`;
}

function rankByTotalScore(items) {
  let previousScore = null;
  let rank = 0;
  return [...items].sort((a, b) => Number(b.total_score) - Number(a.total_score)
    || a.watch_id.localeCompare(b.watch_id, undefined, { numeric: true }))
    .map((item, index) => {
      const score = Number(item.total_score);
      if (score !== previousScore) rank = index + 1;
      previousScore = score;
      return { ...item, rank };
    });
}

function getCurrentOverallRanking(data) {
  const watches = Array.isArray(data.watch_ids) ? data.watch_ids : [];
  return rankByTotalScore(watches.map(watchId => {
    const score = (data.scores || {})[watchId] || {};
    const survival = Number(score.survival_score) || 0;
    const interference = Number(score.interference_score) || 0;
    return {
      watch_id: watchId,
      survival_score: survival,
      interference_score: interference,
      total_score: survival + interference + (Number(score.ranking_bonus) || 0)
    };
  }));
}

async function refreshJengaSeries() {
  try {
    const modeResponse = await fetchShared("/get_control_mode", { cache: "no-store" });
    if (!modeResponse.ok) return;
    const mode = (await modeResponse.json()).mode;
    const res = await fetchShared(mode === "team_coop" ? "/coop_status" : "/jenga_series", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    displayedStateTimes = mode !== "team_coop" && data.scoring_mode === "state"
      ? Object.fromEntries((data.state_ranking || []).map(entry => [entry.watch_id, entry])) : null;
    renderStateTimes();
    latestJengaSeries = data;
    renderSetStatus();
    if (mode === "team_coop") {
      const history = document.getElementById("game-score-history");
      if (history) {
        const complete = !data.active && data.set_finished;
        const ranking = rankByTotalScore(Object.entries(data.team_scores || {}).map(([watch_id, total_score]) => ({ watch_id, total_score })));
        const setCards = (data.set_history || []).map(result =>
          `<section class="game-score-history-item set-row"><h3>SET ${escapeResultText(result.set)} 終了</h3>
          <div>倒壊: ${escapeResultText(result.collapsed_player || "なし（終了ボタン）")}</div>
          <div>チームA: ${Number(result.team_scores?.team_a) || 0}点 / チームB: ${Number(result.team_scores?.team_b) || 0}点</div></section>`
        ).join('');
        history.classList.toggle('has-final-results', Boolean(complete));
        setHTMLIfChanged(history, complete
          ? renderFinalResults(ranking, data.teams || {}) + `<details class="result-breakdown"><summary>各セットの結果を見る</summary><div class="result-breakdown-cards">${setCards}</div></details>`
          : setCards);
      }
      return;
    }
    const setSelector = document.getElementById("jengaSetCount");
    if (setSelector && !data.active && setSelector.value !== String(data.total_sets)) {
      setSelector.value = String(data.total_sets);
    }
    const history = document.getElementById("game-score-history");
    const scoreHistory = Array.isArray(data.set_history) ? data.set_history : [];
    const setCards = scoreHistory.map(result => {
      const scores = Object.entries(result.scores || {})
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([watchId, score]) => `<li class="result-set-score"><span class="result-player-name">${renderResultWatchLabel(watchId)}</span><strong class="result-value">${escapeResultText(score.total_score || 0)}点</strong></li>`)
        .join("");
      const collapsedPlayer = result.collapsed_player || "なし（終了ボタン）";
      return `<section class="game-score-history-item set-row">
        <h3 class="result-heading">SET ${escapeResultText(result.set)} 終了</h3>
        <div class="result-detail">倒壊: ${escapeResultText(collapsedPlayer)}${result.mvp ? ` / 妨害MVP: ${escapeResultText(result.mvp)}` : ''}</div>
        <ul class="result-set-scores">${scores}</ul>
      </section>`;
    }).join("");
    const interferenceRanking = Array.isArray(data.interference_ranking) ? data.interference_ranking : [];
    const stateRanking = Array.isArray(data.state_ranking) ? data.state_ranking : [];
    const finalRanking = rankByTotalScore(Array.isArray(data.final_ranking) ? data.final_ranking : []);
    const currentRanking = getCurrentOverallRanking(data);
    history.classList.toggle('has-final-results', finalRanking.length > 0);
    if (finalRanking.length) {
      setHTMLIfChanged(history, renderFinalResults(finalRanking) +
        `<details class="result-breakdown"><summary>各セットの結果・得点内訳を見る</summary><div class="result-breakdown-cards">${setCards}</div></details>`);
      return;
    }
    setHTMLIfChanged(history, [
      !finalRanking.length && data.active && currentRanking.length
        ? renderRankingCard("現在の総合順位（暫定）", currentRanking, item => `${item.total_score}点`) : '',
      attackChallengeModes.has(currentControlMode) && interferenceRanking.length ? renderRankingCard("現在の妨害順位", interferenceRanking, item => `${item.success_count}回`, false, true) : '',
      data.scoring_mode === "state" && stateRanking.length
        ? renderRankingCard("ノルマ達成側の時間順位（このセット）", stateRanking, item => `${(item.quota_keep_ms / 1000).toFixed(1)}秒`) : '',
      setCards
    ].join(""));
  } catch (e) {
    console.error("連続ゲーム情報の取得に失敗", e);
  }
}

async function stopGame() {
  isGameRunning = false;
  try {
    const res = await fetchShared('/stop', { method: 'POST' });
    const data = await res.json();
    console.log('[JS] POST /stop ->', data);

    if (typeof stopPlotting === "function") {
      stopPlotting();
    }
    clearInterval(turnCountdownTimer);
    setTurnCountdown('', false);
    setGamePhaseBanner('ゲーム終了', 'stop');
    setTimeout(() => setGamePhaseBanner('待機', 'ready'), 1500);
    await refreshGameStatus();
    document.getElementById('csvBtn').style.display = 'inline-block';
    const heartEl = document.getElementById("heartRateDisplay");
    if (heartEl) {
      heartEl.innerHTML = '';
    }
    alert('ゲームを終了しました');
  } catch (error) {
    console.error(error);
    alert('終了リクエスト失敗');
  }
}

async function resetServer() {
  if (!confirm("本当にリセットしますか？全データを消去します。")) return;
  try {
    const res = await fetchShared('/reset', { method: 'POST' });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') {
      throw new Error(data.message || 'リセットに失敗しました');
    }
    for (const key of Object.keys(maxHeartRates)) delete maxHeartRates[key];
    for (const timer of Object.values(baselineTimers)) clearTimeout(timer);
    for (const timer of Object.values(baselineIntervals)) clearInterval(timer);
    for (const key of ['maxHeartRates', 'fetchingStatus', 'finishedPlayers', 'babanukiLogs']) {
      localStorage.removeItem(key);
    }
    alert('リセットが完了しました');
    window.location.reload();
  } catch (error) {
    console.error(error);
    alert('リセットリクエスト失敗');
  }
}

let baselineTimers = {};
let baselineIntervals = {};

async function calculateBaseline() {
  const selectedId = document.getElementById("baselineSelector").value;
  if (!selectedId) return alert("デバイスIDが選択されていません");

  const resultEl = document.getElementById("baseline-result");

  if (baselineTimers[selectedId]) {
    clearTimeout(baselineTimers[selectedId]);
    clearInterval(baselineIntervals[selectedId]);
  }

  try {
    await fetchShared("/start_baseline", { method: "POST" });
    await new Promise(r => setTimeout(r, 1000));

    let countdown = 10;

    baselineIntervals[selectedId] = setInterval(() => {
      countdown--;
      resultEl.innerText = `${selectedId} の平均値取得中... (${countdown}秒)`;
      console.log("心拍数取得中...");
    }, 1000);

    baselineTimers[selectedId] = setTimeout(async () => {
      clearInterval(baselineIntervals[selectedId]);

      const res = await fetchShared(`/calculate_baseline/${selectedId}`, {
        method: "POST"
      });

      const data = await res.json();

      if (res.ok) {
        resultEl.innerText = `${selectedId} の平均値取得完了`;
        updateBaselineUI(selectedId, data.average);

        setTimeout(() => {
          resultEl.innerText = "";
        }, 3000);
      } else {
        resultEl.innerText = `${selectedId} の平均値取得に失敗：${data.message || data.error || 'エラー'}`;
      }

      await fetchShared("/stop_baseline", { method: "POST" });

      delete baselineTimers[selectedId];
      delete baselineIntervals[selectedId];
    }, 10000);
  } catch (err) {
    console.error(err);
    resultEl.innerText = "baseline取得エラー";
  }
}

let turnCountdownTimer = null;
let lastCountdownTurn = null;
let lastAttackScoringMode = null;

function setGamePhaseBanner(label, tone = 'ready') {
  const banner = document.getElementById('game-phase-banner');
  if (!banner) return;
  banner.textContent = label;
  banner.style.background = tone === 'start'
    ? 'linear-gradient(180deg, #e8f5e9 0%, #a5d6a7 100%)'
    : tone === 'stop'
      ? 'linear-gradient(180deg, #ffebee 0%, #ef9a9a 100%)'
      : 'linear-gradient(180deg, #e3f2fd 0%, #bbdefb 100%)';
  banner.style.borderColor = tone === 'start' ? '#66bb6a' : tone === 'stop' ? '#ef5350' : '#90caf9';
  banner.style.color = tone === 'start' ? '#1b5e20' : tone === 'stop' ? '#b71c1c' : '#0d47a1';
}

function setTurnCountdown(message, visible = true) {
  const banner = document.getElementById('turn-countdown-banner');
  if (!banner) return;
  banner.textContent = message;
  banner.classList.toggle('visible', visible);
}

async function runTurnCountdown(seconds = 5) {
  const banner = document.getElementById('turn-countdown-banner');
  if (!banner) return;

  const currentTurnRes = await fetchShared('/turn', { cache: 'no-store' });
  const currentTurnData = await currentTurnRes.json();
  const currentTurn = currentTurnData.current_turn;
  if (!currentTurn) {
    setTurnCountdown('待機中', false);
    return;
  }

  lastCountdownTurn = currentTurn;
  clearInterval(turnCountdownTimer);

  const labels = Array.from({ length: seconds }, (_, index) => String(seconds - index));
    labels.push('GO');
  let index = 0;
  setTurnCountdown(labels[index]);

  turnCountdownTimer = setInterval(() => {
    index += 1;
    if (index >= labels.length) {
      clearInterval(turnCountdownTimer);
      setTurnCountdown('GO', true);
      setTimeout(() => setTurnCountdown('', false), 900);
      return;
    }
    setTurnCountdown(labels[index]);
  }, 1000);
}

function updateCollapseWatchSelector(ids = null) {
  const selector = document.getElementById("collapseWatchSelector");
  if (!selector) return;
  selector.options[0].textContent = displayedCurrentTurn
    ? `現在の手番（自動）：${displayedCurrentTurn}` : "現在の手番（自動）";
  if (ids === null) return;
  const selected = selector.value;
  const watches = [...new Set(ids)].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  const existing = Array.from(selector.options).slice(1).map(option => option.value);
  if (JSON.stringify(existing) === JSON.stringify(watches)) return;
  while (selector.options.length > 1) selector.remove(1);
  for (const watch of watches) {
    const option = document.createElement("option");
    option.value = watch;
    option.textContent = watch;
    selector.appendChild(option);
  }
  selector.value = watches.includes(selected) ? selected : "";
}

async function refreshCurrentTurn() {
  try {
    const res = await fetchShared('/turn');
    const data = await res.json();
    displayedCurrentTurn = data.current_turn || null;
    updateCollapseWatchSelector();
    highlightCurrentTurn();
    const turnNumber = Number(data.turn_number);
    const turnLabel = Number.isInteger(turnNumber) && turnNumber > 0
      ? `第${turnNumber}ターン　`
      : '';
    let display = data.current_turn
      ? `${turnLabel}watch ${data.current_turn.slice(-1)} のターンです`
      : '全員受付中';
    document.getElementById('current-turn').innerText = '今のターン: ' + display;
    document.getElementById('turn-display-large').innerText = display;

    const modeRes = await fetchShared('/get_control_mode', { cache: 'no-store' });
    const mode = (await modeRes.json()).mode;
    const gameRunning = document.getElementById('game-status').textContent.includes('開始中');
    if (mode === 'attack_challenge_wait' && gameRunning) {
      const current = data.current_turn;
      if (!turnCountdownTimer || lastCountdownTurn !== current) {
        runTurnCountdown(5);
      }
    } else {
      clearInterval(turnCountdownTimer);
      setTurnCountdown('', false);
    }
  } catch (error) {
    console.error(error);
    document.getElementById('current-turn').innerText = '今のターン: 取得失敗';
    document.getElementById('turn-display-large').innerText = '';
  }
}

async function refreshScores() {
  try {
    const res = await fetchShared('/scores', { cache: 'no-store' });
    if (!res.ok) throw new Error('score fetch failed');
    const scores = await res.json();
    const board = document.getElementById('score-board');
    setHTMLIfChanged(board, Object.entries(scores)
      .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
      .map(([watchId, score]) => {
        if (typeof score === "object" && score !== null) {
          return `<div class="score-item">${renderResultWatchLabel(watchId)}: ${score.total_score || 0}点<br><small>生存 ${score.survival_score || 0} / 妨害 ${score.interference_score || 0} / ボーナス ${score.ranking_bonus || 0}</small></div>`;
        }
        return `<div class="score-item">${renderResultWatchLabel(watchId)}: ${score}点</div>`;
      })
      .join(''));
  } catch (error) {
    console.error(error);
  }
}

function calculateSelectedBaseline() {
  calculateBaseline();
}

async function refreshClientList() {
  const selector = document.getElementById("turnSelector");
  const baselineSelector = document.getElementById("baselineSelector");
  selector.innerHTML = "";
  baselineSelector.innerHTML = "";

  try {
    const res = await fetchShared("/clients");
    const data = await res.json();

    updateCollapseWatchSelector(Object.values(data.ids));
    document.getElementById('watch-count').innerText = `接続中のデバイス数: ${data.count}`;
    for (const ip in data.ids) {
      const id = data.ids[ip];
      const option1 = document.createElement("option");
      option1.value = id;
      option1.textContent = id;
      selector.appendChild(option1);

      const option2 = document.createElement("option");
      option2.value = id;
      option2.textContent = id;
      baselineSelector.appendChild(option2);
    }
  } catch (e) {
    console.error(e);
    document.getElementById('watch-count').innerText = '接続中のデバイス数: 取得失敗';
  }
}

function updateBaselineUI(device_id, avg) {
  let elem = document.getElementById(`baseline-${device_id}`);

  if (!elem) {
    elem = document.createElement("div");
    elem.id = `baseline-${device_id}`;
    document.getElementById("baseline-area").appendChild(elem);
  }

  elem.innerText = `${device_id} の平均値：${Math.round(avg)} BPM`;
}

async function loadBaselineToUI() {
  try {
    const res = await fetchShared("/get_baselines");
    const data = await res.json();

    const area = document.getElementById("baseline-area");
    area.innerHTML = "";

    for (const device_id in data) {
      updateBaselineUI(device_id, data[device_id]);
    }
  } catch (e) {
    console.error("baseline復元失敗", e);
  }
}

function setTurn() {
  const selectedId = document.getElementById("turnSelector").value;
  fetchShared("/set_turn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_turn: selectedId })
  })
    .then(res => res.json())
    .then(data => {
      alert(data.message);
      refreshCurrentTurn();
      refreshScores();
    })
    .catch(err => console.error(err));
}

async function nextTurn() {
  try {
    const modeRes = await fetchShared('/get_control_mode', { cache: 'no-store' });
    const mode = (await modeRes.json()).mode;

    const clientRes = await fetchShared("/clients");
    const turnRes = await fetchShared("/turn");

    const clients = await clientRes.json();
    const current = (await turnRes.json()).current_turn;

    const ids = Object.values(clients.ids).sort();
    const idx = ids.indexOf(current);
    const nextId = ids[(idx + 1) % ids.length];

    if (mode === 'attack_challenge_wait') {
      setTurnCountdown('待機中', false);
      for (let i = 5; i >= 1; i -= 1) {
        setTurnCountdown(`${i}`);
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      setTurnCountdown('GO');
      await new Promise(resolve => setTimeout(resolve, 400));
    }

    const res = await fetchShared("/set_turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_turn: nextId })
    });

    const result = await res.json();

    if (!res.ok) {
      alert(result.message || "ターン変更失敗");
      return;
    }

    setTurnCountdown('', false);
    refreshCurrentTurn();
    refreshScores();
  } catch (e) {
    console.error(e);
    alert("ターン変更に失敗しました");
  }
}

async function exportCSV() {
  const inputName = prompt("CSVファイル名を入力してください（空欄なら自動名で保存）", "");

  let url = "/export_csv";

  if (inputName !== null && inputName.trim() !== "") {
    url += "?filename=" + encodeURIComponent(inputName.trim());
  }

  try {
    const res = await fetchShared(url);

    if (!res.ok) {
      const msg = await res.text();
      alert("CSV保存に失敗: " + msg);
      return;
    }

    const blob = await res.blob();
    const downloadUrl = window.URL.createObjectURL(blob);
    const a = document.createElement("a");

    a.href = downloadUrl;
    a.download = inputName && inputName.trim() !== ""
      ? inputName.trim().replace(/\.csv$/i, "") + ".csv"
      : "heart_rate_data.csv";

    document.body.appendChild(a);
    a.click();
    a.remove();

    window.URL.revokeObjectURL(downloadUrl);
  } catch (error) {
    console.error(error);
    alert("CSV保存に失敗しました");
  }
}

async function recordCollapse() {
  const notes = document.getElementById("collapseNotes").value;
  const statusEl = document.getElementById("collapse-status");

  try {
    const res = await fetchShared("/collapse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: "倒壊",
        notes: notes,
        watch_id: document.getElementById("collapseWatchSelector")?.value || undefined
      })
    });

    const data = await res.json();

    if (res.ok) {
      const collapseSelector = document.getElementById("collapseWatchSelector");
      if (collapseSelector) collapseSelector.value = "";
      statusEl.innerText = `${data.watch_id} の倒壊を記録しました。` + (data.series_complete ? "✓ 最終セットを確定しました" : "✓ セット得点を確定しました。次セットへ進めます");
      statusEl.style.color = "#4caf50";
      document.getElementById("collapseNotes").value = "";
      await refreshScores();
      await refreshJengaSeries();
      await refreshGameStatus();
      isGameRunning = false;
      stopPlotting();

      setTimeout(() => {
        statusEl.innerText = "";
      }, 3000);
    } else {
      statusEl.innerText = `✗ ${data.message || "記録に失敗しました"}`;
      statusEl.style.color = "#f44336";
    }
  } catch (error) {
    console.error(error);
    statusEl.innerText = "✗ 通信エラーが発生しました";
    statusEl.style.color = "#f44336";
  }
}

function getRandomColor() {
  return `hsl(${Math.floor(Math.random() * 360)}, 70%, 50%)`;
}

window.onload = async () => {
  const prevStatus = localStorage.getItem("fetchingStatus");
  if (prevStatus === "running") {
    startFetching();
  } else {
    document.getElementById('status').innerText = '状態: 停止';
  }

  await loadCurrentMode();
  await loadAttackScoring();
  await loadCurrentRotationDirection();
  await loadCurrentRotationHold();
  await updateModeButtons();
  pollWithoutOverlap(updateModeButtons, 2000);
  pollWithoutOverlap(refreshGameStatus, 2000);
  pollWithoutOverlap(refreshCurrentTurn, 1000);
  // 管理画面で変更したSET情報と得点方式を、両画面へ定期反映する。
  pollWithoutOverlap(refreshJengaSeries, 1000);
  pollWithoutOverlap(loadAttackScoring, 1000);
  // 定期的に現在モードとベースラインを取得して、別画面での変更を即時反映する
  pollWithoutOverlap(loadCurrentMode, 1500);
  pollWithoutOverlap(loadBaselineToUI, 3000);

  window.addEventListener("load", async () => {
    const res = await fetchShared("/get_baselines");
    const data = await res.json();

    for (const device in data) {
      const avg = Math.round(data[device]);
      const elem = document.getElementById(`baseline-${device}`);

      if (elem) {
        elem.innerText = `${device} の平均値: ${Math.round(avg)} BPM`;
      }
    }
  });

  await refreshGameStatus();
  await refreshCurrentTurn();
  await refreshScores();
  await refreshJengaSeries();
  await refreshCurrentTarget();
  await refreshClientList();
  await setupGraphs();
  await loadBaselineToUI();
  await loadManualRotationStatus();

  try {
    const res = await fetchShared('/status');
    const status = await res.json();
    if (status.running) {
      isGameRunning = true;
      startPlotting();
    }
  } catch (e) {
    console.error("ゲーム状態取得失敗", e);
  }
  await restoreBaselineStatus();
};

async function refreshCurrentTarget() {
  try {
    const resTurn = await fetchShared('/turn');
    const turn = await resTurn.json();
    const current = turn.current_turn;
    displayedCurrentTurn = current || null;
    highlightCurrentTurn();
    const referenceDisplay = document.getElementById('comparison-reference');

    if (!current) {
      if (referenceDisplay) referenceDisplay.innerText = '回転基準の心拍数: 手番未設定';
      document.getElementById('current-target').innerText = '利用対象: 未設定';
      const usedEl = document.getElementById('current-used');
      if (usedEl) usedEl.innerText = '利用中の心拍: 未設定';
      setHighlightedHeartTarget(null);
      return;
    }

    const modeResponse = await fetchShared('/get_control_mode', { cache: 'no-store' });
    const controlMode = (await modeResponse.json()).mode;
    if (controlMode === 'team_coop') {
      if (referenceDisplay) referenceDisplay.innerText = '協力モード：サポート成功に応じて回転速度が変わります';
      const coopResponse = await fetchShared('/coop_status', { cache: 'no-store' });
      const coop = await coopResponse.json();
      const teams = coop.teams || {};
      coopTeamByWatch = {};
      Object.entries(teams).forEach(([teamId, members]) => {
        (members || []).forEach(watchId => { coopTeamByWatch[watchId] = teamId; });
      });
      const teamDisplay = document.getElementById('coop-team-display');
      if (teamDisplay) {
        teamDisplay.style.display = 'grid';
        teamDisplay.innerHTML = `
          <div class="coop-team-card coop-team-a"><strong>チームA</strong><span>${(teams.team_a || []).join('・') || '未設定'}</span><b>${coop.team_scores?.team_a || 0}点</b></div>
          <div class="coop-team-card coop-team-b"><strong>チームB</strong><span>${(teams.team_b || []).join('・') || '未設定'}</span><b>${coop.team_scores?.team_b || 0}点</b></div>`;
      }
      const state = coop.turn_state || {};
      const isDown = state.phase === 'down';
      const phaseClass = isDown ? 'challenge-direction-down' : 'challenge-direction-up';
      const phaseLabel = isDown ? '下げよう' : '上げよう';
      const heartbeat = Number(state.heartbeat);
      const threshold = Number(state.threshold);
      const hasHeartbeat = state.heartbeat != null && Number.isFinite(heartbeat);
      const hasThreshold = state.threshold != null && Number.isFinite(threshold);
      const remaining = hasHeartbeat && hasThreshold ? Math.max(0, isDown ? heartbeat - threshold : threshold - heartbeat) : null;
      const status = state.success ? '達成' : '挑戦中';
      const statusClass = state.success ? 'status-success' : 'status-pending';
      const attackEl = document.getElementById('attack-status');
      const details = document.getElementById('attack-details');
      const usedEl = document.getElementById('current-used');
      document.getElementById('current-target').innerText = `サポート役: ${state.supporter || '未設定'}`;
      if (usedEl) usedEl.innerText = `プレイヤー: ${state.current_turn || current} / サポート: ${state.supporter || '未設定'} / 回転速度: ${state.rpm || 40} rpm`;
      setHighlightedHeartTarget(state.supporter);
      if (attackEl) attackEl.innerText = state.success
        ? '協力サポート成功：10 RPMへ段階的に減速中'
        : '協力サポート挑戦中：ノルマ到達で回転が落ち着きます';
      if (details) details.innerHTML = `
        <div class="challenge-heading"><h3>協力サポート</h3><span class="${phaseClass}">${isDown ? "▼" : "▲"} 心拍数を${phaseLabel}</span></div>
        <div class="challenge-players">
          <article class="challenge-player ${statusClass} challenge-phase-${isDown ? 'down' : 'up'}">
            <div class="challenge-player-heading"><strong>${state.supporter || 'サポート役未設定'}</strong>
              <span class="attack-status-badge ${statusClass}">${status}</span></div>
            <div class="challenge-goal-row">
              <div class="challenge-goal"><span>目標心拍数（ノルマ）</span><strong>${hasThreshold ? `${isDown ? Math.floor(threshold) : Math.ceil(threshold)} <small>BPM ${isDown ? "以下" : "以上"}</small>` : '未設定'}</strong></div>
              <div class="challenge-direction-cue ${phaseClass}"><span class="challenge-big-arrow" aria-hidden="true"></span><strong>${phaseLabel}</strong></div>
            </div>
            <div class="challenge-current"><span>現在の心拍数</span><strong>${hasHeartbeat ? `${Math.round(heartbeat)} <small>BPM</small>` : '未取得'}</strong></div>
            <p class="challenge-guidance">${state.success ? 'サポート成功！回転がゆっくりになります' : remaining == null ? '心拍数・ノルマの取得を待っています' : `あと ${Math.ceil(remaining)} BPM ${phaseLabel}`}</p>
          </article>
        </div>`;
      return;
    }

    coopTeamByWatch = {};
    const teamDisplay = document.getElementById('coop-team-display');
    if (teamDisplay) {
      teamDisplay.style.display = 'none';
      teamDisplay.innerHTML = '';
    }

    const res = await fetchShared('/get_rotation_status');
    const data = await res.json();
    const info = data[current] || {};
    const target = info.target_watch || info.target || '';
    const rpm = Number.isFinite(Number(info.rpm)) ? Number(info.rpm) : null;
    const directionNames = { c: '反時計回り', a: '時計回り' };
    const direction = directionNames[info.direction] || '未設定';
    const extremeNames = { up: '基準より最も上昇', down: '基準より最も下降' };
    const extreme = extremeNames[info.extreme] || '';
    const attackers = Array.isArray(info.attackers) ? info.attackers : [];

    document.getElementById('current-target').innerText = `利用対象: ${target || '未設定'}`;
    const usedEl = document.getElementById('current-used');
    if (usedEl) {
      const speed = rpm === null ? '未設定' : `${rpm} rpm`;
      const selectedExtreme = extreme ? ` / 採用: ${extreme}` : '';
      usedEl.innerText = `利用中の心拍: ${target || '未設定'}${selectedExtreme} / 回転速度: ${speed} / 回転方向: ${direction}`;
    }
    const highlightDifference = ['highest_diff', 'lowest_diff', 'random_diff'].includes(controlMode)
      && info.mode === controlMode;
    setHighlightedHeartTarget(highlightDifference ? target : null);
    const referenceEl = document.getElementById('comparison-reference');
    if (referenceEl) {
      const references = info.mode === controlMode ? { ...(info.reference_heartbeats || {}) } : {};
      if (info.mode === controlMode && !Object.keys(references).length && target && info.reference_bpm != null) {
        references[target] = info.reference_bpm;
      }
      const sourceLabel = info.reference_source === 'turn_start' ? 'このターン開始時の心拍' : '平均心拍';
      const lines = Object.entries(references)
        .sort(([firstWatch], [secondWatch]) => firstWatch.localeCompare(secondWatch))
        .map(([watchId, heartbeat]) => {
          const bpm = heartbeat == null ? NaN : Number(heartbeat);
          return Number.isFinite(bpm) ? `${watchId}: ${Math.round(bpm)} BPM` : `${watchId}: 未設定`;
        });
      referenceEl.innerText = lines.length
        ? `回転基準の心拍数（${sourceLabel}）\n${lines.join('\n')}`
        : '回転基準の心拍数: 取得待ち';
    }
    const attackEl = document.getElementById('attack-status');
    const attackDetailsEl = document.getElementById('attack-details');
    if (attackEl && attackDetailsEl) {
      const attackRes = await fetchShared('/attack_status');
      const attackData = await attackRes.json();
      const activeAttackers = Array.isArray(attackData.attackers) ? attackData.attackers : attackers;
      const pending = Array.isArray(attackData.pending_attackers) ? attackData.pending_attackers : [];
      const conditionNames = { up: '上昇', down: '下降' };

      if (!attackData.attack_mode) {
        attackEl.innerText = activeAttackers.length
          ? `妨害情報：現在参加 ${activeAttackers.join(', ')} (${activeAttackers.length}台)`
          : '妨害情報：現在参加 なし (0台)';
        attackDetailsEl.innerHTML = '';
        return;
      }

      const participants = [...new Set([...activeAttackers, ...pending])];
      const requirements = attackData.challenge_requirements || {};

      // Header summary
      attackEl.innerText = `妨害情報：現在参加 ${participants.length ? participants.join(', ') : 'なし'} (${participants.length}台) / 条件：${conditionNames[attackData.challenge_direction] || '未設定'}`;

      const expandedChallenge = Boolean(attackDetailsEl.closest('.heart-display-expanded'));
      const escapeChallengeText = (value) => String(value).replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[char]));

      // Build participant readings and goals.
      const rows = participants.map((watchId) => {
        const requirement = requirements[watchId] || {};
        const rawThreshold = requirement.threshold;
        const threshold = rawThreshold == null ? null : Number(rawThreshold);
        const heartbeat = requirement.heartbeat == null ? NaN : Number(requirement.heartbeat);
        const referenceBpm = requirement.reference_bpm == null ? NaN : Number(requirement.reference_bpm);
        const referenceLabel = requirement.reference_source === 'turn_start' ? '交代時' : '平均値';
        const status = requirement.status || (activeAttackers.includes(watchId) ? '達成' : '挑戦中');
        const directionKey = attackData.challenge_direction === 'down' ? 'down' : 'up';
        const currentTrend = Number.isFinite(heartbeat) && Number.isFinite(referenceBpm)
          ? (heartbeat > referenceBpm ? 'up' : heartbeat < referenceBpm ? 'down' : 'equal')
          : 'unknown';
        const currentCell = Number.isFinite(heartbeat)
          ? `<strong class="current-value current-${currentTrend}">${Math.round(heartbeat)} BPM</strong>`
          : '<strong class="current-value current-unknown">未取得</strong>';
        const referenceCell = Number.isFinite(referenceBpm)
          ? `<strong class="reference-value">${Math.round(referenceBpm)} BPM</strong><span class="reference-note">${referenceLabel}</span>`
          : '<strong class="reference-value">未設定</strong>';
        const thresholdCell = Number.isFinite(threshold)
          ? `<strong>${Math.round(threshold)} BPM</strong>`
          : '<strong>未設定</strong>';

        const statusClass = status === '達成' ? 'status-success' : status === '挑戦中' ? 'status-pending' : 'status-none';

        const arrowGlyph = directionKey === 'down' ? '▼' : '▲';
        const arrowClass = directionKey === 'down' ? 'down' : 'up';
        if (expandedChallenge) {
          const directionKnown = ['up', 'down'].includes(attackData.challenge_direction);
          const directionText = directionKey === 'down' ? '下げよう' : '上げよう';
          const directionClass = directionKnown ? `challenge-direction-${directionKey}` : '';
          const hasGoal = Number.isFinite(threshold);
          const hasReading = Number.isFinite(heartbeat);
          const remaining = hasGoal && hasReading
            ? Math.max(0, directionKey === 'down' ? heartbeat - threshold : threshold - heartbeat)
            : null;
          const goal = hasGoal
            ? `${directionKey === 'down' ? Math.floor(threshold) : Math.ceil(threshold)} <small>BPM ${directionKey === 'down' ? '以下' : '以上'}</small>`
            : '未設定';
          const guidance = status === '達成' ? 'このターンのチャレンジは達成済みです'
            : remaining === null ? '心拍数・ノルマの取得を待っています'
            : remaining === 0 ? '目標に到達しています'
            : `あと ${Math.ceil(remaining)} BPM <strong class="${directionClass}">${directionText}</strong>`;
          return `<article class="challenge-player ${statusClass}${directionKnown ? ` challenge-phase-${directionKey}` : ''}">
            <div class="challenge-player-heading"><strong>${escapeChallengeText(watchId)}</strong>
              <span class="attack-status-badge ${statusClass}">${status === '達成' ? '✓ 達成済み' : escapeChallengeText(status)}</span></div>
            ${status === '達成' ? '<div class="challenge-achieved-label"><span aria-hidden="true">✓</span> チャレンジ達成！</div>' : ''}
            <div class="challenge-goal-row">
              <div class="challenge-goal"><span>目標心拍数（ノルマ）</span><strong>${goal}</strong></div>
              ${directionKnown && status !== '達成' ? `<div class="challenge-direction-cue ${directionClass}">
                <span class="challenge-big-arrow" aria-hidden="true"></span>
                <strong>${directionText}</strong>
              </div>` : ''}
            </div>
            <div class="challenge-current"><span>現在の心拍数</span><strong>${hasReading ? `${Math.round(heartbeat)} <small>BPM</small>` : '未取得'}</strong></div>
            <p class="challenge-guidance">${guidance}</p>
            <div class="challenge-reference">基準（${referenceLabel}）：${Number.isFinite(referenceBpm) ? `${Math.round(referenceBpm)} BPM` : '未設定'}</div>
          </article>`;
        }

        return `
          <tr class="${statusClass}">
            <td class="arrow-cell"><span class="attack-arrow small ${arrowClass}">${arrowGlyph}</span></td>
            <td><strong>${escapeChallengeText(watchId)}</strong></td>
            <td class="value-${currentTrend}">${currentCell}</td>
            <td class="reference-cell">${referenceCell}</td>
            <td class="value-${directionKey}">${thresholdCell}</td>
            <td><span class="attack-status-badge ${statusClass}">${status === '達成' ? '✓ 達成済み' : escapeChallengeText(status)}</span></td>
          </tr>
        `;
      }).join('\n');

      const tableHtml = `
        <table class="attack-table" role="table">
          <thead>
            <tr>
              <th class="arrow-cell">方向</th>
              <th>プレイヤー</th>
              <th>現在</th>
              <th>参照</th>
              <th>ノルマ</th>
              <th>状態</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      `;
      setHTMLIfChanged(attackDetailsEl, expandedChallenge
        ? `<div class="challenge-heading"><h3>妨害チャレンジ</h3><span class="${attackData.challenge_direction === 'down' ? 'challenge-direction-down' : attackData.challenge_direction === 'up' ? 'challenge-direction-up' : ''}">${attackData.challenge_direction === 'down' ? '▼ 心拍数を下げよう' : attackData.challenge_direction === 'up' ? '▲ 心拍数を上げよう' : '条件を設定してください'}</span></div>
           <div class="challenge-players">${rows || '<p class="challenge-empty">参加者を待っています</p>'}</div>`
        : tableHtml);
    }
  } catch (e) {
    console.error('refreshCurrentTarget failed', e);
  }
}

pollWithoutOverlap(refreshCurrentTarget, 1000);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    console.log("復帰: グラフ強制更新");
    fetchHeartData();
    refreshCurrentTurn();
  }
});

function startHeartDataLoop() {
  if (!document.getElementById("graph-area")) return;
  startPlotting();
}

document.addEventListener('keydown', async (event) => {
  if (event.key === 'Enter') {
    await nextTurn();
  }
});

let watchIds = [];
let isGameRunning = false;

const charts = {};
const dataBuffers = {};

function createGraph(watchId) {
  const container = document.getElementById("graph-area");
  const div = document.createElement("div");
  div.className = "watch-graph";
  div.innerHTML = `
    <h3>${watchId}</h3>
    <div class="chart-wrapper">
      <canvas id="chart-${watchId}"></canvas>
    </div>
  `;
  container.appendChild(div);

  const canvas = div.querySelector('canvas');
  const ctx = canvas.getContext('2d');

  dataBuffers[watchId] = [];

  const chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: '心拍数',
        data: [],
        borderColor: 'red',
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.3
      }]
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: true,
      aspectRatio: 1,
      scales: {
        x: {
          type: 'linear',
          min: 0,
          max: 30,
          reverse: true,
          title: {
            display: true,
            text: '時間（秒）'
          },
          ticks: {
            stepSize: 5,
            callback: function(value) {
              return `${value}秒前`;
            }
          }
        },
        y: {
          min: 60,
          max: 120,
          title: {
            display: true,
            text: 'BPM'
          }
        }
      }
    }
  });
  charts[watchId] = chart;
}

async function fetchHeartData() {
  if (!isGameRunning || !document.getElementById("graph-area")) return;

  const response = await fetchShared('/get_heart_data');
  const data = await response.json();

  const now = Date.now();

  Object.entries(data).forEach(([watchId, records]) => {
    if (!charts[watchId]) return;
    const chart = charts[watchId];

    const bpmData = records.map(r => ({
      x: ((now - r.timestamp) / 1000),
      y: r.heartbeat
    }));

    chart.data.datasets[0].data = bpmData;
    chart.update("none");
  });
}

async function setupGraphs() {
  if (!document.getElementById("graph-area")) return;
  try {
    const res = await fetchShared('/clients');
    const data = await res.json();
    watchIds = Object.values(data.ids);
    const container = document.getElementById("graph-area");

    for (const id in charts) {
      try {
        charts[id].destroy();
      } catch (e) {
        console.warn('chart destroy failed for', id, e);
      }
      delete charts[id];
    }
    for (const id in dataBuffers) delete dataBuffers[id];

    container.innerHTML = "";

    for (const watchId of watchIds) {
      createGraph(watchId);
    }

    fetchHeartData();
  } catch (err) {
    console.error("グラフ初期化失敗:", err);
  }
}

let plotInterval = null;

function startPlotting() {
  if (!document.getElementById("graph-area")) return;
  if (plotInterval) clearInterval(plotInterval);

  plotInterval = pollWithoutOverlap(fetchHeartData, 1000);
}

function stopPlotting() {
  if (plotInterval) {
    clearInterval(plotInterval);
    plotInterval = null;
  }
}

function updateChart(watchId, heartbeat, timestamp) {
  const dataset = charts[watchId].data.datasets[0];
  const labels = charts[watchId].data.labels;

  dataset.data.push(heartbeat);
  labels.push("none");

  if (dataset.data.length > 30) {
    dataset.data.shift();
    labels.shift();
  }

  charts[watchId].update();
}

async function setMode(mode) {
  try {
    const resClient = await fetchShared("/clients");
    const dataClient = await resClient.json();
    const count = dataClient.count || 0;

    if (["next_fast", "prev_fast", "random_fast"].includes(mode) && count < 2) {
      showBanner("他人の心拍モードは2台以上接続時のみ使用できます");
      return;
    }

    const res = await fetchShared("/set_control_mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode })
    });

    const data = await res.json();

    if (!res.ok) {
      showBanner(data.message || "モード変更に失敗しました");
      return;
    }

    const label = getModeLabel(mode);
    currentControlMode = mode;
    updateAttackScoringVisibility();
    renderSetStatus();
    document.getElementById("mode-current").innerText = `現在の設定：${label}`;
    showBanner(`モード変更：${label}`);
  } catch (e) {
    console.error(e);
    showBanner("モード変更通信エラー");
  }
}

function getModeLabel(mode) {
  const modeNames = {
    self_fast: "自分の心拍（差が大きいほど速い）",
    self_slow: "自分の心拍（差が大きいほど遅い）",
    next_fast: "他人の心拍（次の人）",
    prev_fast: "他人の心拍（前の人）",
    random_fast: "他人の心拍（ランダム）",
    highest_diff: "基準値より最も上がった人",
    lowest_diff: "基準値より最も下がった人",
    random_diff: "上昇・下降をターンごとランダム",
    attack_challenge: "妨害チャレンジ",
    attack_challenge_wait: "待ち時間あり妨害チャレンジ",
    manual_test: "手動テストモード"
  };
  return modeNames[mode] || mode;
}

function getDirectionLabel(direction) {
  const directionNames = {
    auto: "自動",
    c: "反時計回り",
    a: "時計回り"
  };
  return directionNames[direction] || direction;
}

async function setRotationDirection(direction) {
  try {
    const res = await fetchShared("/set_rotation_direction", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ direction })
    });

    const data = await res.json();

    if (!res.ok) {
      showBanner(data.message || "回転指定変更に失敗しました");
      return;
    }

    document.getElementById("direction-current").innerText = `回転指定：${getDirectionLabel(direction)}`;
    showBanner(`回転指定：${getDirectionLabel(direction)} に変更しました`);
  } catch (e) {
    console.error(e);
    showBanner("回転指定通信エラー");
  }
}

async function loadCurrentRotationDirection() {
  try {
    const res = await fetchShared("/get_rotation_direction");
    const data = await res.json();
    const label = getDirectionLabel(data.direction);
    document.getElementById("direction-current").innerText = `回転指定：${label}`;
  } catch (e) {
    console.error("回転指定取得失敗", e);
  }
}

function getHoldLabel(hold) {
  return hold ? "5秒キープ" : "即時切替";
}

async function setRotationHold(hold) {
  try {
    const res = await fetchShared("/set_rotation_hold", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({hold})
    });
    const data = await res.json();

    if (!res.ok) {
      showBanner(data.message || "切り替え設定変更に失敗しました");
      return;
    }

    document.getElementById("hold-current").innerText = `切り替え：${getHoldLabel(data.hold)}`;
    showBanner(`切り替え設定：${getHoldLabel(data.hold)} に変更しました`);
  } catch (e) {
    console.error(e);
    showBanner("切り替え設定通信エラー");
  }
}

async function loadCurrentRotationHold() {
  try {
    const res = await fetchShared("/get_rotation_hold");
    const data = await res.json();
    const label = getHoldLabel(data.hold);
    document.getElementById("hold-current").innerText = `切り替え：${label}`;
  } catch (e) {
    console.error("切り替え設定取得失敗", e);
  }
}

function showBanner(message) {
  const banner = document.getElementById("mode-banner");
  banner.textContent = message;
  banner.classList.add("show");

  setTimeout(() => {
    banner.classList.remove("show");
  }, 2000);
}

async function updateModeButtons(runningOverride = null) {
  try {
    const requests = [fetchShared("/clients"), fetchShared("/get_control_mode")];
    if (runningOverride === null) requests.push(fetchShared('/status', { cache: 'no-store' }));
    const responses = await Promise.all(requests);
    const data = await responses[0].json();
    const controlMode = (await responses[1].json()).mode;
    const running = runningOverride === null
      ? Boolean((await responses[2].json()).running)
      : Boolean(runningOverride);
    updateCollapseWatchSelector(Object.values(data.ids || {}));
    const count = data.count || 0;

    const otherButtons = document.querySelectorAll(".requires-two");
    const modeButtons = document.querySelectorAll('button[onclick^="setMode("]');
    const note = document.getElementById("other-mode-note");
    const attackScoringSelector = document.getElementById("attackScoringSelector");
    const startButton = document.querySelector('button[onclick="startGame()"]');
    const manualTestPanel = document.getElementById("manual-test-rotation-panel");
    const manualTestButtons = manualTestPanel ? manualTestPanel.querySelectorAll("button") : [];

    const canUseOtherMode = count >= 2;

    modeButtons.forEach(btn => {
      btn.disabled = running;
    });
    otherButtons.forEach(btn => {
      btn.disabled = running || !canUseOtherMode;
    });
    const attackModes = new Set(["attack_challenge", "attack_challenge_wait"]);
    if (attackScoringSelector) {
      attackScoringSelector.disabled = running || !attackModes.has(controlMode);
    }
    if (manualTestPanel) {
      manualTestPanel.style.display = running ? "none" : "block";
      manualTestButtons.forEach(btn => {
        btn.disabled = running;
      });
    }
    if (startButton) {
      startButton.disabled = running || controlMode === "manual_test";
    }

    if (running) {
      note.innerText = 'ゲーム中はモーター制御モードを変更できません';
    } else if (canUseOtherMode) {
      note.innerText = `接続台数: ${count}台 → 他人の心拍モード使用可`;
    } else {
      note.innerText = `接続台数: ${count}台 → 他人の心拍モードは2台以上で使用可能`;
    }
  } catch (e) {
    console.error("接続台数取得失敗", e);
  }
}

async function loadCurrentMode() {
  try {
    const res = await fetchShared("/get_control_mode");
    const data = await res.json();
    currentControlMode = data.mode;
    updateAttackScoringVisibility();
    renderSetStatus();
    const label = getModeLabel(data.mode);
    document.getElementById("mode-current").innerText = `現在の設定：${label}`;
  } catch (e) {
    console.error("現在モード取得失敗", e);
  }
}

async function setManualRotation(rpm, mode) {
  try {
    const res = await fetchShared("/set_manual_rotation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rpm, mode, enabled: true })
    });
    const data = await res.json();
    if (!res.ok) {
      showBanner(data.message || "手動回転の設定に失敗しました");
      return;
    }
    const modeLabel = mode === "c" ? "反時計回り" : mode === "a" ? "時計回り" : "ランダム";
    const statusEl = document.getElementById("manual-rotation-status");
    statusEl.innerText = `手動テスト: ${rpm} RPM / ${modeLabel}`;
    showBanner(`手動テスト回転: ${rpm} RPM / ${modeLabel}`);
  } catch (e) {
    console.error(e);
    showBanner("手動回転の設定通信エラー");
  }
}

async function clearManualRotation() {
  try {
    const res = await fetchShared("/clear_manual_rotation", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      showBanner(data.message || "手動回転の停止に失敗しました");
      return;
    }
    document.getElementById("manual-rotation-status").innerText = "手動テスト: 停止中";
    showBanner("手動回転テストを停止しました");
  } catch (e) {
    console.error(e);
    showBanner("手動回転解除の通信エラー");
  }
}

async function loadManualRotationStatus() {
  try {
    const res = await fetchShared("/manual_rotation");
    const data = await res.json();
    if (!data.enabled) {
      document.getElementById("manual-rotation-status").innerText = "手動テスト: 停止中";
      return;
    }
    const mode = data.mode || data.direction || "c";
    const modeLabel = mode === "c" ? "反時計回り" : mode === "a" ? "時計回り" : "ランダム";
    document.getElementById("manual-rotation-status").innerText = `手動テスト: ${data.rpm} RPM / ${modeLabel}`;
  } catch (e) {
    console.error("手動テスト状態取得失敗", e);
  }
}

function getAttackScoringLabel(mode) {
  const labels = {
    success: "成功回数: 成功1回ごとに +1点",
    impact: "影響度: セット首位 +1点",
    state: "状態管理: ノルマ維持時間1位にゲーム終了時 +1点",
    mvp: "MVP: セットMVP +1点"
  };
  return labels[mode] || mode;
}

let attackScoringSaving = false;
let attackScoringRevision = 0;

async function loadAttackScoring() {
  if (attackScoringSaving) return;
  const requestRevision = attackScoringRevision;
  try {
    const res = await fetchShared("/attack_scoring", { cache: "no-store" });
    const data = await res.json();
    if (requestRevision !== attackScoringRevision || attackScoringSaving) return;
    const selector = document.getElementById("attackScoringSelector");
    if (selector) selector.value = data.mode;
    updateSetScoreDisplay(data.mode);
  } catch (e) {
    console.error("妨害チャレンジ得点方式取得失敗", e);
  }
}

async function setAttackScoring() {
  const selector = document.getElementById("attackScoringSelector");
  if (!selector) return;
  const selectedMode = selector.value;
  attackScoringSaving = true;
  attackScoringRevision += 1;
  try {
    const res = await fetchShared("/attack_scoring", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: selectedMode })
    });
    const data = await res.json();
    if (!res.ok) {
      showBanner(data.message || "得点方式の変更に失敗しました");
      selector.value = data.mode || selectedMode;
      return;
    }
    selector.value = data.mode;
    updateSetScoreDisplay(data.mode);
    showBanner(`妨害チャレンジ得点方式: ${getAttackScoringLabel(data.mode)}`);
    await refreshJengaSeries();
    await refreshScores();
  } catch (e) {
    console.error(e);
    showBanner("得点方式の変更通信エラー");
    selector.value = selectedMode;
  } finally {
    attackScoringSaving = false;
  }
}

async function setJengaSetCount() {
  const selector = document.getElementById("jengaSetCount");
  if (!selector) return;
  const selectedSets = selector.value;
  try {
    const res = await fetchShared("/jenga_settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ total_sets: Number(selectedSets) })
    });
    const data = await res.json();
    if (!res.ok) {
      showBanner(data.message || "セット数の変更に失敗しました");
      await refreshJengaSeries();
      return;
    }
    selector.value = String(data.total_sets);
    await refreshJengaSeries();
    showBanner(`セット数: ${data.total_sets} SET`);
  } catch (e) {
    console.error(e);
    showBanner("セット数の変更通信エラー");
    await refreshJengaSeries();
  }
}

async function resetGameOnly() {
  if (!confirm("前回ゲームのCSVデータだけを消去しますか？")) return;

  try {
    const res = await fetchShared("/reset_game", { method: "POST" });
    const data = await res.json();

    if (res.ok) {
      await refreshScores();
      await refreshJengaSeries();
      alert("次のゲーム用にリセットしました");
    } else {
      alert(data.message || "ゲームリセットに失敗しました");
    }
  } catch (e) {
    console.error(e);
    alert("ゲームリセット通信エラー");
  }
}

async function restoreBaselineStatus() {
  try {
    const res = await fetchShared('/get_baselines');
    const data = await res.json();
    for (const key in data) {
      const avg = Math.round(data[key]);
      const elem = document.getElementById(`baseline-${key}`);
      if (elem) {
        elem.innerText = `${key} の平均値: ${Math.round(avg)} BPM`;
      }
    }
  } catch (e) {
    console.error('baseline状態復元失敗', e);
  }
}
