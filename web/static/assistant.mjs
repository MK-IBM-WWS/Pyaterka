const $ = (selector) => document.querySelector(selector);
const connect = $('#connect');
const mic = $('#mic-toggle');
const mute = $('#mute-btn');
const rec = $('#rec-btn');
const download = $('#export-btn');
const answer = $('#answer-text');
const agentState = $('#agent-state');
const logPanel = $('#log');
const status = $('#status');
const diagnostics = $('.session-status');
const decoder = new TextDecoder();
const entries = [];
const audioTracks = new Map();
const labels = {listening:'Слушаю', thinking:'Готовлю ответ…', speaking:'Говорю'};
let sdk;
let room;
let micTrack;
let muted = false;
let recording = false;
let audioContext;
let peak = 0;

function log(message) {
  const line = document.createElement('div');
  line.textContent = `[${new Date().toLocaleTimeString('ru-RU')}] ${message}`;
  logPanel.append(line);
  if (logPanel.children.length > 60) logPanel.firstChild.remove();
  logPanel.scrollTop = logPanel.scrollHeight;
}

function setStatus(text, kind) {
  status.textContent = text;
  diagnostics.classList.toggle('active', kind === 'active');
  diagnostics.classList.toggle('error', kind === 'error');
  log(text);
}

function record(role, text) {
  if (!recording || !text) return;
  entries.push({time:new Date(), role, text});
  download.hidden = false;
}

function setAnswer(text, placeholder = false) {
  answer.textContent = text;
  answer.classList.toggle('placeholder', placeholder);
}

function handleData(payload) {
  let event;
  try { event = JSON.parse(decoder.decode(payload)); } catch { return; }
  if (event.type === 'agent_state') {
    agentState.textContent = labels[event.state] ?? '';
    agentState.hidden = !agentState.textContent;
  } else if (event.type === 'agent_speech' && typeof event.text === 'string') {
    setAnswer(event.text);
    record('Пятерка', event.text);
  } else if (event.type === 'user_speech' && typeof event.text === 'string') {
    record('Пользователь', event.text);
  }
}

function voiceLevel() {
  let level = 0;
  for (const {analyser, samples} of audioTracks.values()) {
    if (!analyser || audioContext?.state !== 'running') continue;
    analyser.getFloatTimeDomainData(samples);
    let energy = 0;
    for (const sample of samples) energy += sample * sample;
    level = Math.max(level, Math.sqrt(energy / samples.length));
  }
  if (!audioContext || audioContext.state !== 'running' || ![...audioTracks.values()].some(track => track.analyser)) {
    for (const participant of room?.remoteParticipants.values() ?? []) {
      level = Math.max(level, participant.audioLevel || 0);
    }
  }
  peak = Math.max(.10, level, peak * .991);
  return level < .008 ? 0 : Math.min(1, Math.pow((level - .008) / peak, .62) * 1.25);
}

function closeTrack(track) {
  const playback = audioTracks.get(track);
  if (!playback) return;
  playback.source?.disconnect();
  playback.analyser?.disconnect();
  for (const element of track.detach()) element.remove();
  playback.element.remove();
  audioTracks.delete(track);
}

function attachTrack(track) {
  if (track.kind !== sdk.Track.Kind.Audio || audioTracks.has(track)) return;
  const element = track.attach();
  element.muted = muted;
  element.autoplay = true;
  document.body.append(element);
  element.play().catch(() => log('Для включения звука нажмите «Звук».'));
  const playback = {element};
  audioTracks.set(track, playback);
  if (audioContext) {
    try {
      playback.source = audioContext.createMediaStreamSource(new MediaStream([track.mediaStreamTrack]));
      playback.analyser = audioContext.createAnalyser();
      playback.analyser.fftSize = 1024;
      playback.samples = new Float32Array(playback.analyser.fftSize);
      playback.source.connect(playback.analyser);
    } catch (error) {
      log('Анализ звука недоступен: ' + error.message);
    }
  }
}

function syncControls() {
  connect.textContent = room ? 'Отключиться' : 'Подключиться';
  mic.disabled = !micTrack;
  mute.disabled = !room;
  mic.textContent = micTrack?.isMuted ? 'Включить микрофон' : 'Микрофон';
  mute.textContent = muted ? 'Включить звук' : 'Звук';
  mic.setAttribute('aria-pressed', String(Boolean(micTrack && !micTrack.isMuted)));
  mute.setAttribute('aria-pressed', String(Boolean(room && !muted)));
}

function clearRoom(activeRoom) {
  if (room !== activeRoom) return;
  for (const track of audioTracks.keys()) closeTrack(track);
  micTrack?.stop();
  micTrack = null;
  room = null;
  muted = false;
  agentState.hidden = true;
  syncControls();
}

async function token() {
  const id = 'voice_' + crypto.randomUUID().replaceAll('-', '');
  const response = await fetch('/token?room=' + id, {
    signal: AbortSignal.timeout(10000), credentials:'same-origin', cache:'no-store',
  });
  if (!response.ok) {
    const {error} = await response.json().catch(() => ({}));
    const messages = {
      'server busy':'Все голосовые сессии заняты. Попробуйте чуть позже.',
      'too many requests':'Слишком много попыток. Подождите минуту.',
      'livekit unavailable':'Голосовой сервер временно недоступен.',
    };
    throw new Error(messages[error] || `Сервер подключения вернул ошибку (${response.status}).`);
  }
  const result = await response.json();
  if (!result.url || !result.token) throw new Error('Сервер не вернул данные для подключения.');
  return result;
}

connect.onclick = async () => {
  connect.disabled = true;
  let activeRoom = room;
  try {
    if (activeRoom) {
      await activeRoom.disconnect();
      clearRoom(activeRoom);
      setStatus('Готов к подключению');
      return;
    }
    if (!isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      throw new Error('Откройте сайт по HTTPS с доверенным сертификатом для доступа к микрофону.');
    }
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!audioContext && Context) {
      try { audioContext = new Context(); } catch {}
    }
    audioContext?.resume().catch(() => {});
    setStatus('Подключение…');
    setAnswer('Подключение к Пятерке…', true);
    sdk ??= await import('./vendor/livekit-client-2.5.0.esm.mjs');
    const credentials = await token();
    activeRoom = new sdk.Room();
    room = activeRoom;
    activeRoom.on(sdk.RoomEvent.TrackSubscribed, (track) => {
      if (room === activeRoom) attachTrack(track);
    });
    activeRoom.on(sdk.RoomEvent.TrackUnsubscribed, (track) => {
      if (room === activeRoom) closeTrack(track);
    });
    activeRoom.on(sdk.RoomEvent.DataReceived, (payload) => {
      if (room === activeRoom) handleData(payload);
    });
    activeRoom.on(sdk.RoomEvent.Disconnected, () => {
      if (room !== activeRoom) return;
      clearRoom(activeRoom);
      setStatus('Готов к подключению');
    });
    await activeRoom.connect(credentials.url, credentials.token, {websocketTimeout:10000, peerConnectionTimeout:15000, maxRetries:1, rtcConfig:{iceServers:[]}});
    activeRoom.startAudio().catch(() => {});
    const track = await sdk.createLocalAudioTrack({echoCancellation:true, noiseSuppression:true});
    if (room !== activeRoom) {
      track.stop();
      throw new Error('Голосовое соединение прервалось.');
    }
    micTrack = track;
    await activeRoom.localParticipant.publishTrack(track);
    if (room !== activeRoom) throw new Error('Голосовое соединение прервалось.');
    if (answer.classList.contains('placeholder')) setAnswer('Задайте Пятерке вопрос голосом.', true);
    syncControls();
    setStatus('Соединение активно', 'active');
  } catch (error) {
    clearRoom(activeRoom);
    if (activeRoom) await activeRoom.disconnect().catch(() => {});
    const message = error.name === 'NotAllowedError' ? 'Разрешите доступ к микрофону в браузере.' : error.message;
    setStatus(message, 'error');
    if (answer.classList.contains('placeholder')) setAnswer('Подключитесь и задайте Пятерке вопрос голосом.', true);
  } finally {
    connect.disabled = false;
  }
};

mic.onclick = async () => {
  if (!micTrack) return;
  try {
    if (micTrack.isMuted) await micTrack.unmute();
    else await micTrack.mute();
    syncControls();
  } catch (error) { log(error.message); }
};

mute.onclick = async () => {
  if (!room) return;
  if (!room.canPlaybackAudio) {
    await room.startAudio().catch(() => {});
    audioContext?.resume().catch(() => {});
    for (const {element} of audioTracks.values()) element.play().catch(() => {});
    return;
  }
  muted = !muted;
  for (const {element} of audioTracks.values()) element.muted = muted;
  syncControls();
};

rec.onclick = () => {
  recording = !recording;
  rec.setAttribute('aria-pressed', String(recording));
  rec.textContent = recording ? 'Остановить запись' : 'Запись';
};
download.onclick = () => {
  const text = ['# Диалог с Пятеркой · кафедра ИУ5', '', ...entries.map(({time, role, text}) =>
    `**[${time.toLocaleTimeString('ru-RU')}] ${role}**\n\n${text}\n`)].join('\n');
  const uri = URL.createObjectURL(new Blob([text], {type:'text/markdown;charset=utf-8'}));
  const link = Object.assign(document.createElement('a'), {href:uri, download:'iu5-dialog.md'});
  link.click();
  setTimeout(() => URL.revokeObjectURL(uri), 500);
};

import('./owl.mjs')
  .then(({mountOwl}) => mountOwl($('#avatar'), voiceLevel))
  .catch((error) => log('3D-аватар недоступен: ' + error.message));
