let intervalId = null;
const maxHeartRates = JSON.parse(localStorage.getItem("maxHeartRates") || "{}");

const MAX_POINTS = 30;

let heartDataInterval = null;

function startFetching() {
  if (intervalId !== null) return;
  intervalId = setInterval(() => {
    fetchHeartRate();
    refreshCurrentTurn();
    refreshScores();
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
    const res = await fetch('/heart_all');
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
    rateContainer.innerHTML = '';
    maxContainer.innerHTML = '';

    if (!data || Object.keys(data).length === 0) {
      rateContainer.innerText = 'データがありません';
      maxContainer.innerText = '最大心拍数を記録できません';
    } else {
      for (const [device_id, record] of Object.entries(data)) {
        const bpm = record.heartbeat;
        const div = document.createElement('div');
        const bpmText = (bpm !== undefined && bpm !== null) ? `${bpm}` : "--";
        div.innerText = `心拍数: ${bpmText} bpm (${device_id})`;
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

      for (const [device_id, maxBpm] of Object.entries(maxHeartRates)) {
        const div = document.createElement('div');
        div.innerText = `最大心拍数: ${maxBpm} bpm (${device_id})`;
        div.style.fontSize = '1.5em';
        div.style.fontWeight = 'bold';
        maxContainer.appendChild(div);
      }
    }
  } catch (error) {
    document.getElementById('rate').innerText = '取得エラー';
    console.error('取得中にエラーが発生しました:', error);
  }
}

async function refreshGameStatus() {
  try {
    const res = await fetch('/status', { cache: "no-store" });
    const data = await res.json();
    console.log('[DEBUG] /status data:', data);
    document.getElementById('game-status').innerText = 'ゲーム状態: ' + (data.running ? '開始中' : '終了');
    await updateModeButtons(data.running);
  } catch (error) {
    console.error(error);
    document.getElementById('game-status').innerText = 'ゲーム状態： 取得失敗';
  }
}

async function startGame() {
  try {
    const totalSets = document.getElementById("jengaSetCount").value;
    const res = await fetch(`/start?mode=jenga&sets=${encodeURIComponent(totalSets)}`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      alert("ゲームを開始しました");
      isGameRunning = true;
      refreshGameStatus();
      refreshJengaSeries();
      refreshScores();
      setupGraphs();
      startPlotting();
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
    const statusRes = await fetch("/status", { cache: "no-store" });
    const status = await statusRes.json();
    if (status.running) {
      alert("ゲーム中です。先に「終了」ボタンでゲームを終了してください");
      return;
    }

    if (!confirm("現在の得点を維持して、次のゲームへ進みますか？")) return;

    const res = await fetch("/next_jenga_game", { method: "POST" });
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

async function refreshJengaSeries() {
  try {
    const res = await fetch("/jenga_series", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    const modeLabels = {
      success: "妨害成功型",
      impact: "影響度型",
      ranking: "累積順位型",
      mvp: "MVP型"
    };
    document.getElementById("game-number").innerText = `SET ${data.game_number || 1} / ${data.total_sets || 3}　得点方式: ${modeLabels[data.scoring_mode] || "未設定"}`;
    const history = document.getElementById("game-score-history");
    const scoreHistory = Array.isArray(data.set_history) ? data.set_history : [];
    history.innerHTML = scoreHistory.map(result => {
      const scores = Object.entries(result.scores || {})
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([watchId, score]) => `${watchId}: ${score.total_score || 0}点`)
        .join(" / ");
      const mvp = result.mvp ? ` / 妨害MVP: ${result.mvp}` : "";
      return `
        <div class="game-score-history-item set-row">
          <div class="info-label">SET</div>
          SET ${result.set} 終了 / 倒壊: ${result.collapsed_player}${mvp}<br>${scores}
        </div>
      `;
    }).join("");
    const interferenceRanking = Array.isArray(data.interference_ranking) ? data.interference_ranking : [];
    if (interferenceRanking.length) {
      history.innerHTML += `
        <div class="game-score-history-item rank-row">
          <div class="info-label">順位</div>
          現在の妨害順位: ${interferenceRanking.map(item => `${item.rank}位 ${item.watch_id} (${item.success_count}回)`).join(" / ")}
        </div>
      `;
    }
    const finalRanking = Array.isArray(data.final_ranking) ? data.final_ranking : [];
    if (finalRanking.length) {
      history.innerHTML += `
        <div class="game-score-history-item rank-row">
          <div class="info-label">総合</div>
          最終総合順位: ${finalRanking.map(item => `${item.rank}位 ${item.watch_id} (${item.total_score}点)`).join(" / ")}
        </div>
      `;
    }
  } catch (e) {
    console.error("連続ゲーム情報の取得に失敗", e);
  }
}

async function stopGame() {
  isGameRunning = false;
  try {
    const res = await fetch('/stop', { method: 'POST' });
    const data = await res.json();
    console.log('[JS] POST /stop ->', data);

    if (typeof stopPlotting === "function") {
      stopPlotting();
    }
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
    const res = await fetch('/reset', { method: 'POST' });
    const data = await res.json();
    localStorage.removeItem("maxHeartRates");
    document.getElementById('rate').innerHTML = '<p>読み込み中...</p>';
    document.getElementById('max-rate').innerHTML = '<p>最大心拍数を記録中...</p>';
    document.getElementById('status').innerText = '状態: 停止';
    await refreshGameStatus();
    await refreshCurrentTurn();
    await refreshClientList();
    alert('リセットが完了しました');
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
    await fetch("/start_baseline", { method: "POST" });
    await new Promise(r => setTimeout(r, 1000));

    let countdown = 10;

    baselineIntervals[selectedId] = setInterval(() => {
      countdown--;
      resultEl.innerText = `${selectedId} の平均値取得中... (${countdown}秒)`;
      console.log("心拍数取得中...");
    }, 1000);

    baselineTimers[selectedId] = setTimeout(async () => {
      clearInterval(baselineIntervals[selectedId]);

      const res = await fetch(`/calculate_baseline/${selectedId}`, {
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

      await fetch("/stop_baseline", { method: "POST" });

      delete baselineTimers[selectedId];
      delete baselineIntervals[selectedId];
    }, 10000);
  } catch (err) {
    console.error(err);
    resultEl.innerText = "baseline取得エラー";
  }
}

async function refreshCurrentTurn() {
  try {
    const res = await fetch('/turn');
    const data = await res.json();
    const turnNumber = Number(data.turn_number);
    const turnLabel = Number.isInteger(turnNumber) && turnNumber > 0
      ? `第${turnNumber}ターン　`
      : '';
    let display = data.current_turn
      ? `${turnLabel}watch ${data.current_turn.slice(-1)} のターンです`
      : '全員受付中';
    document.getElementById('current-turn').innerText = '今のターン: ' + display;
    document.getElementById('turn-display-large').innerText = display;
  } catch (error) {
    console.error(error);
    document.getElementById('current-turn').innerText = '今のターン: 取得失敗';
    document.getElementById('turn-display-large').innerText = '';
  }
}

async function refreshScores() {
  try {
    const res = await fetch('/scores', { cache: 'no-store' });
    if (!res.ok) throw new Error('score fetch failed');
    const scores = await res.json();
    const board = document.getElementById('score-board');
    board.innerHTML = Object.entries(scores)
      .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
      .map(([watchId, score]) => {
        if (typeof score === "object" && score !== null) {
          return `<div class="score-item">${watchId}: ${score.total_score || 0}点<br><small>生存 ${score.survival_score || 0} / 妨害 ${score.interference_score || 0} / 順位 ${score.ranking_bonus || 0}</small></div>`;
        }
        return `<div class="score-item">${watchId}: ${score}点</div>`;
      })
      .join('');
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
    const res = await fetch("/clients");
    const data = await res.json();

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
    const res = await fetch("/get_baselines");
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
  fetch("/set_turn", {
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
    const clientRes = await fetch("/clients");
    const turnRes = await fetch("/turn");

    const clients = await clientRes.json();
    const current = (await turnRes.json()).current_turn;

    const ids = Object.values(clients.ids).sort();
    const idx = ids.indexOf(current);
    const nextId = ids[(idx + 1) % ids.length];

    const res = await fetch("/set_turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_turn: nextId })
    });

    const result = await res.json();

    if (!res.ok) {
      alert(result.message || "ターン変更失敗");
      return;
    }

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
    const res = await fetch(url);

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
    const res = await fetch("/collapse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: "倒壊",
        notes: notes
      })
    });

    const data = await res.json();

    if (res.ok) {
      statusEl.innerText = data.series_complete ? "✓ 最終セットを確定しました" : "✓ セット得点を確定しました。次セットへ進めます";
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
      statusEl.innerText = "✗ 記録に失敗しました";
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
  setInterval(updateModeButtons, 2000);

  window.addEventListener("load", async () => {
    const res = await fetch("/get_baselines");
    const data = await res.json();

    for (const device in data) {
      const avg = Math.round(data[device]);
      const elem = document.getElementById(`baseline-${device}`);

      if (elem) {
        elem.innerText = `${device} の平均値: ${avg} BPM`;
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
    const res = await fetch('/status');
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
    const resTurn = await fetch('/turn');
    const turn = await resTurn.json();
    const current = turn.current_turn;

    if (!current) {
      document.getElementById('current-target').innerText = '利用対象: 未設定';
      const usedEl = document.getElementById('current-used');
      if (usedEl) usedEl.innerText = '利用中の心拍: 未設定';
      return;
    }

    const res = await fetch('/get_rotation_status');
    const data = await res.json();
    const info = data[current] || {};
    const target = info.target_watch || info.target || '';
    const rpm = Number.isFinite(Number(info.rpm)) ? Number(info.rpm) : null;
    const directionNames = { c: '時計回り', a: '反時計回り' };
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
    const referenceEl = document.getElementById('comparison-reference');
    if (referenceEl) {
      const references = info.reference_heartbeats || {};
      const sourceLabel = info.reference_source === 'turn_start' ? 'ターン交代時の心拍' : '平均値';
      const lines = Object.entries(references)
        .sort(([firstWatch], [secondWatch]) => firstWatch.localeCompare(secondWatch))
        .map(([watchId, heartbeat]) => {
          const bpm = Number(heartbeat);
          return Number.isFinite(bpm) ? `${watchId}: ${Math.round(bpm)} BPM` : `${watchId}: 未設定`;
        });
      referenceEl.innerText = lines.length
        ? `比較の参考心拍 (${sourceLabel})\n${lines.join('\n')}`
        : '比較の参考心拍: 未設定';
    }
    const attackEl = document.getElementById('attack-status');
    if (attackEl) {
      const attackRes = await fetch('/attack_status');
      const attackData = await attackRes.json();
      const activeAttackers = Array.isArray(attackData.attackers) ? attackData.attackers : attackers;
      const pending = Array.isArray(attackData.pending_attackers) ? attackData.pending_attackers : [];
      const conditionNames = { up: '上昇', down: '下降' };
      if (attackData.attack_mode && referenceEl) {
        const requirements = attackData.challenge_requirements || {};
        const referenceLines = Object.entries(requirements)
          .sort(([firstWatch], [secondWatch]) => firstWatch.localeCompare(secondWatch))
          .map(([watchId, requirement]) => {
            const bpm = Number(requirement.reference_bpm);
            if (!Number.isFinite(bpm)) return `${watchId}: 未設定`;
            const label = requirement.reference_source === 'turn_start'
              ? 'ターン交代時'
              : '平均値';
            return `${watchId}: ${Math.round(bpm)} BPM (${label})`;
          });
        referenceEl.innerText = referenceLines.length
          ? `比較の参考心拍\n${referenceLines.join('\n')}`
          : '比較の参考心拍: 未設定';
      }
      if (!attackData.attack_mode) {
        attackEl.innerText = activeAttackers.length
          ? `妨害情報：現在参加 ${activeAttackers.join(', ')} (${activeAttackers.length}台)`
          : '妨害情報：現在参加 なし (0台)';
        return;
      }

      const participants = [...new Set([...activeAttackers, ...pending])];
      const requirements = attackData.challenge_requirements || {};
      const participantLines = participants.map((watchId) => {
        const requirement = requirements[watchId] || {};
        const threshold = Number(requirement.threshold);
        const heartbeat = Number(requirement.heartbeat);
        const target = Number.isFinite(threshold) ? `${Math.round(threshold)} BPM` : '未設定';
        const current = Number.isFinite(heartbeat) ? `現在 ${Math.round(heartbeat)} BPM / ` : '';
        const status = requirement.status || (activeAttackers.includes(watchId) ? '達成' : '挑戦中');
        return `${watchId}：${current}ノルマ ${target} / ${status}`;
      });
      const participantText = participants.length ? participants.join(', ') : 'なし';
      attackEl.innerText = [
        `妨害情報：現在参加 ${participantText} (${participants.length}台)`,
        `条件：${conditionNames[attackData.challenge_direction] || '未設定'}`,
        ...participantLines,
      ].join('\n');
    }
  } catch (e) {
    console.error('refreshCurrentTarget failed', e);
  }
}

setInterval(refreshCurrentTarget, 1000);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    console.log("復帰: グラフ強制更新");
    fetchHeartData();
    refreshCurrentTurn();
  }
});

let fetchHeartDataIntervalId = null;

function startHeartDataLoop() {
  if (heartDataInterval) return;
  heartDataInterval = setInterval(fetchHeartData, 1000);
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
  if (!isGameRunning) return;

  const response = await fetch('/get_heart_data');
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
    chart.update();
  });
}

async function setupGraphs() {
  try {
    const res = await fetch('/clients');
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
  if (plotInterval) clearInterval(plotInterval);

  plotInterval = setInterval(async () => {
    if (!isGameRunning) return;

    const response = await fetch("/get_heart_data");
    const data = await response.json();

    const now = Date.now();
    const past30s = now - 30000;

    for (const watchId in data) {
      const chart = charts[watchId];
      if (!chart) continue;

      const filtered = data[watchId].filter(r => r.timestamp >= past30s);
      const baseTime = filtered.length > 0 ? filtered[0].timestamp : now;

      chart.data.labels = filtered.map(r => ((now - r.timestamp) / 1000).toFixed(0));
      chart.data.datasets[0].data = filtered.map(r => ({
        x: ((now - r.timestamp) / 1000),
        y: r.heartbeat
      }));

      chart.update();
    }
  }, 1000);
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
    const resClient = await fetch("/clients");
    const dataClient = await resClient.json();
    const count = dataClient.count || 0;

    if (["next_fast", "prev_fast", "random_fast"].includes(mode) && count < 2) {
      showBanner("他人の心拍モードは2台以上接続時のみ使用できます");
      return;
    }

    const res = await fetch("/set_control_mode", {
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
    manual_test: "手動テストモード"
  };
  return modeNames[mode] || mode;
}

function getDirectionLabel(direction) {
  const directionNames = {
    auto: "自動",
    c: "時計回り",
    a: "反時計回り"
  };
  return directionNames[direction] || direction;
}

async function setRotationDirection(direction) {
  try {
    const res = await fetch("/set_rotation_direction", {
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
    const res = await fetch("/get_rotation_direction");
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
    const res = await fetch("/set_rotation_hold", {
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
    const res = await fetch("/get_rotation_hold");
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
    const requests = [fetch("/clients"), fetch("/get_control_mode")];
    if (runningOverride === null) requests.push(fetch('/status', { cache: 'no-store' }));
    const responses = await Promise.all(requests);
    const data = await responses[0].json();
    const controlMode = (await responses[1].json()).mode;
    const running = runningOverride === null
      ? Boolean((await responses[2].json()).running)
      : Boolean(runningOverride);
    const count = data.count || 0;

    const otherButtons = document.querySelectorAll(".requires-two");
    const modeButtons = document.querySelectorAll('button[onclick^="setMode("]');
    const note = document.getElementById("other-mode-note");
    const attackScoringSelector = document.getElementById("attackScoringSelector");

    const canUseOtherMode = count >= 2;

    modeButtons.forEach(btn => {
      btn.disabled = running;
    });
    otherButtons.forEach(btn => {
      btn.disabled = running || !canUseOtherMode;
    });
    attackScoringSelector.disabled = running || controlMode !== "attack_challenge";

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
    const res = await fetch("/get_control_mode");
    const data = await res.json();
    const label = getModeLabel(data.mode);
    document.getElementById("mode-current").innerText = `現在の設定：${label}`;
  } catch (e) {
    console.error("現在モード取得失敗", e);
  }
}

async function setManualRotation(rpm, mode) {
  try {
    const res = await fetch("/set_manual_rotation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rpm, mode, enabled: true })
    });
    const data = await res.json();
    if (!res.ok) {
      showBanner(data.message || "手動回転の設定に失敗しました");
      return;
    }
    const modeLabel = mode === "c" ? "時計回り" : mode === "a" ? "反時計回り" : "ランダム";
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
    const res = await fetch("/clear_manual_rotation", { method: "POST" });
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
    const res = await fetch("/manual_rotation");
    const data = await res.json();
    if (!data.enabled) {
      document.getElementById("manual-rotation-status").innerText = "手動テスト: 停止中";
      return;
    }
    const mode = data.mode || data.direction || "c";
    const modeLabel = mode === "c" ? "時計回り" : mode === "a" ? "反時計回り" : "ランダム";
    document.getElementById("manual-rotation-status").innerText = `手動テスト: ${data.rpm} RPM / ${modeLabel}`;
  } catch (e) {
    console.error("手動テスト状態取得失敗", e);
  }
}

function getAttackScoringLabel(mode) {
  const labels = {
    success: "成功回数: 成功 +1点",
    impact: "影響度: セット首位 +1点",
    ranking: "累積順位: 最終1位 +2点 / 2位 +1点",
    mvp: "MVP: セットMVP +1点"
  };
  return labels[mode] || mode;
}

async function loadAttackScoring() {
  try {
    const res = await fetch("/attack_scoring");
    const data = await res.json();
    document.getElementById("attackScoringSelector").value = data.mode;
  } catch (e) {
    console.error("妨害チャレンジ得点方式取得失敗", e);
  }
}

async function setAttackScoring() {
  const selector = document.getElementById("attackScoringSelector");
  try {
    const res = await fetch("/attack_scoring", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: selector.value })
    });
    const data = await res.json();
    if (!res.ok) {
      showBanner(data.message || "得点方式の変更に失敗しました");
      await loadAttackScoring();
      return;
    }
    showBanner(`妨害チャレンジ得点方式: ${getAttackScoringLabel(data.mode)}`);
  } catch (e) {
    console.error(e);
    showBanner("得点方式の変更通信エラー");
    await loadAttackScoring();
  }
}

async function resetGameOnly() {
  if (!confirm("前回ゲームのCSVデータだけを消去しますか？")) return;

  try {
    const res = await fetch("/reset_game", { method: "POST" });
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
    const res = await fetch('/get_baselines');
    const data = await res.json();
    for (const key in data) {
      const avg = Math.round(data[key]);
      const elem = document.getElementById(`baseline-${key}`);
      if (elem) {
        elem.innerText = `${key} の平均値: ${avg} BPM`;
      }
    }
  } catch (e) {
    console.error('baseline状態復元失敗', e);
  }
}
