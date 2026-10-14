import React, { useState } from "react";
import { Layers, LogIn } from "lucide-react";
import { api } from "../services/api";

export default function Login({ onLoggedIn }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLoggedIn(await api.login(username, password));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950 text-slate-100">
      <form onSubmit={submit} className="w-full max-w-sm bg-slate-900 border border-slate-800 rounded-xl p-6 space-y-4">
        <h1 className="text-lg font-bold flex items-center gap-2">
          <Layers className="h-5 w-5 text-indigo-400" /> S&amp;OP Workbench
        </h1>
        <div>
          <label htmlFor="username" className="text-xs text-slate-400 block mb-1">Username</label>
          <input id="username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username"
            className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-indigo-500" />
        </div>
        <div>
          <label htmlFor="password" className="text-xs text-slate-400 block mb-1">Password</label>
          <input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password"
            className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-indigo-500" />
        </div>
        {error && <div role="alert" className="text-xs text-rose-400">{error}</div>}
        <button type="submit" disabled={busy || !username || !password}
          className="w-full py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded-lg text-sm font-semibold flex items-center justify-center gap-2">
          <LogIn className="h-4 w-4" /> Sign in
        </button>
      </form>
    </div>
  );
}
