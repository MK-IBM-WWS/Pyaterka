import { mountOwl } from './owl.mjs';

const $ = (selector) => document.querySelector(selector);
const controlsToggle = $('#controls-toggle');
const controls = $('#controls');
const connect = $('#connect');
const mic = $('#mic-toggle');
const mute = $('#mute-btn');
const answer = $('#answer-text');
const feedback = $('#control-error');
const owlActivity = $('#owl-activity');
const setOwlState = mountOwl($('#avatar'), voiceLevel);
const decoder = new TextDecoder();
const audioTracks = new Map();
const labels = {idle:'', listening:'Слушаю', thinking:'Готовлю ответ…', speaking:'Говорю'};
const instruction = answer.textContent;
let sdk;
let room;
let micTrack;
let muted = false;
let agentState = 'idle';
let audioContext;
let peak = 0;

function setControlsOpen(open) {
  const label = open ? 'Скрыть кнопки' : 'Показать кнопки';
  controls.hidden = !open;
  controlsToggle.setAttribute('aria-expanded', String(open));
  controlsToggle.setAttribute('aria-label', label);
  controlsToggle.title = label;
}

setControlsOpen(matchMedia('(min-width: 1300px)').matches);
controlsToggle.onclick = () => setControlsOpen(controls.hidden);
controls.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    setControlsOpen(false);
    controlsToggle.focus();
  }
});

function showError(message = '') {
  feedback.textContent = message;
  feedback.hidden = !message;
}

function syncOwl() {
  const microphoneOff = !micTrack || micTrack.isMuted;
  const state = agentState === 'listening' && microphoneOff ? 'idle' : agentState;
  setOwlState(state);
  owlActivity.textContent = labels[state];
}

function setAnswer(text, placeholder = false) {
  answer.textContent = text;
  answer.classList.toggle('placeholder', placeholder);
}

function handleData(payload) {
  let event;
  try { event = JSON.parse(decoder.decode(payload)); } catch { return; }
  if (event.type === 'agent_state' && Object.hasOwn(labels, event.state)) {
    agentState = event.state;
    syncOwl();
  } else if (event.type === 'agent_speech' && typeof event.text === 'string') {
    setAnswer(event.text);
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
  element.play().catch(() => {
    if (audioTracks.has(track)) showError('Чтобы включить озвучивание, нажмите «Звук».');
  });
  const playback = {element};
  audioTracks.set(track, playback);
  if (audioContext) {
    try {
      playback.source = audioContext.createMediaStreamSource(new MediaStream([track.mediaStreamTrack]));
      playback.analyser = audioContext.createAnalyser();
      playback.analyser.fftSize = 1024;
      playback.samples = new Float32Array(playback.analyser.fftSize);
      playback.source.connect(playback.analyser);
    } catch {
      // LiveKit exposes an audio level when the browser cannot create an analyser.
      playback.analyser = null;
    }
  }
}

function syncControls() {
  connect.textContent = room ? 'Отключиться' : 'Подключиться';
  mic.disabled = !micTrack;
  mute.disabled = !room;
  mic.title = micTrack?.isMuted ? 'Включить микрофон' : 'Выключить микрофон';
  mute.title = muted ? 'Включить звук' : 'Выключить звук';
  mic.setAttribute('aria-pressed', String(Boolean(micTrack && !micTrack.isMuted)));
  mute.setAttribute('aria-pressed', String(Boolean(room && !muted)));
  syncOwl();
}

function clearRoom(activeRoom) {
  if (room !== activeRoom) return;
  for (const track of audioTracks.keys()) closeTrack(track);
  micTrack?.stop();
  micTrack = null;
  room = null;
  muted = false;
  agentState = 'idle';
  if (answer.classList.contains('placeholder')) setAnswer(instruction, true);
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
  showError();
  let activeRoom = room;
  try {
    if (activeRoom) {
      await activeRoom.disconnect();
      clearRoom(activeRoom);
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
  } catch (error) {
    clearRoom(activeRoom);
    if (activeRoom) await activeRoom.disconnect().catch(() => {});
    const message = error.name === 'NotAllowedError' ? 'Разрешите доступ к микрофону в браузере.' : error.message;
    showError(message);
  } finally {
    connect.disabled = false;
  }
};

mic.onclick = async () => {
  if (!micTrack) return;
  showError();
  try {
    if (micTrack.isMuted) await micTrack.unmute();
    else await micTrack.mute();
    syncControls();
  } catch {
    showError('Не удалось переключить микрофон.');
  }
};

mute.onclick = async () => {
  if (!room) return;
  showError();
  if (!room.canPlaybackAudio) {
    await room.startAudio().catch(() => {});
    audioContext?.resume().catch(() => {});
    for (const {element} of audioTracks.values()) {
      element.play().catch(() => showError('Не удалось включить звук в браузере.'));
    }
    return;
  }
  muted = !muted;
  for (const {element} of audioTracks.values()) element.muted = muted;
  syncControls();
};

