type Session = {
  id: string;
  title: string;
  createdAt: number;
  messages: Message[];
};

type Message = {
  id: string;
  role: 'user' | 'ai';
  content: string;
  timestamp: number;
  dsl?: string;
};

type Skill = {
  id: string;
  name: string;
  description: string;
};

let sessions = $state<Session[]>([
  {
    id: 's_default',
    title: '默认会话',
    createdAt: Date.now(),
    messages: [],
  },
]);

let currentSessionId = $state<string>('s_default');
let streaming = $state<boolean>(false);
let messages = $state<Message[]>([]);
let skills = $state<Skill[]>([]);
let connectionStatus = $state<'connecting' | 'ok' | 'error'>('connecting');

let currentSession = $derived(
  sessions.find((s) => s.id === currentSessionId) ?? sessions[0],
);

let sessionCount = $derived(sessions.length);

function uid(): string {
  return 's_' + Math.random().toString(36).slice(2, 10);
}

function newSession(title = '新会话'): Session {
  const s: Session = {
    id: uid(),
    title,
    createdAt: Date.now(),
    messages: [],
  };
  sessions = [s, ...sessions];
  currentSessionId = s.id;
  messages = [];
  return s;
}

function removeSession(id: string): void {
  if (sessions.length <= 1) return;
  sessions = sessions.filter((s) => s.id !== id);
  if (currentSessionId === id) {
    currentSessionId = sessions[0]?.id ?? '';
  }
}

function activateSession(id: string): void {
  if (currentSessionId === id) return;
  currentSessionId = id;
  const s = sessions.find((s) => s.id === id);
  messages = s?.messages ?? [];
}

function addMessage(role: 'user' | 'ai', content: string, dsl?: string): Message {
  const m: Message = {
    id: 'm_' + Math.random().toString(36).slice(2, 10),
    role,
    content,
    timestamp: Date.now(),
    dsl,
  };
  messages = [...messages, m];
  const s = sessions.find((s) => s.id === currentSessionId);
  if (s) {
    s.messages = messages;
  }
  return m;
}

function setSkills(list: Skill[]): void {
  skills = list;
}

function setConnectionStatus(status: 'connecting' | 'ok' | 'error'): void {
  connectionStatus = status;
}

export {
  sessions as $sessions,
  currentSessionId as $currentSessionId,
  streaming as $streaming,
  messages as $messages,
  skills as $skills,
  connectionStatus as $connectionStatus,
  currentSession as $currentSession,
  sessionCount as $sessionCount,
  newSession,
  removeSession,
  activateSession,
  addMessage,
  setSkills,
  setConnectionStatus,
};

export type { Session, Message, Skill };