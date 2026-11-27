import React, { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";
import { DataTable, ErrorNote, Panel } from "../components/ui";
import ChannelConnections from "../components/ChannelConnections";
import AgentConsole from "../components/AgentConsole";

const when = (iso) => (iso ? String(iso).slice(0, 16).replace("T", " ") : "—");

export default function ChannelsPage({ role }) {
  const [types, setTypes] = useState([]);
  const [connections, setConnections] = useState([]);
  const [stock, setStock] = useState([]);
  const [orders, setOrders] = useState([]);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    try {
      const [t, c, inv, o, ev] = await Promise.all([api.channelTypes(), api.channelConnections(), api.channelInventory(),
        api.channelOrders(50), api.channelEvents(30)]);
      setTypes(t.types);
      setConnections(c.connections);
      setStock(inv.by_sku);
      setOrders(o.orders);
      setEvents(ev.events);
    } catch (e) {
      setError(e.message);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const nameOf = (cid) => (connections.find((c) => c.id === cid) || {}).name || cid;

  return (
    <main className="flex-1 px-8 py-6 space-y-6" data-testid="channels-page">
      <ErrorNote error={error} />
      {message && <div className="bg-emerald-950/50 border border-emerald-800 text-emerald-200 rounded-lg px-4 py-3 text-xs"
        data-testid="channels-message">{message}</div>}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div className="xl:col-span-7">
          <ChannelConnections role={role} types={types} connections={connections} onChanged={load}
            onError={setError} onMessage={setMessage} />
        </div>
        <div className="xl:col-span-5">
          <AgentConsole role={role} onError={setError} onChanged={load} />
        </div>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <Panel title="Stock across channels" className="xl:col-span-5" testId="channel-stock">
          <DataTable testId="channel-stock-table" empty="No stock synced yet." rows={stock.map((s) => ({ ...s, id: s.sku }))}
            columns={[
              { key: "sku", label: "SKU" },
              { key: "total", label: "Total", right: true },
              { key: "channels", label: "Per channel", render: (s) => Object.entries(s.channels)
                .map(([cid, q]) => `${nameOf(cid)}: ${q}`).join(" · ") },
            ]} />
        </Panel>
        <Panel title="Recent orders" className="xl:col-span-4" testId="channel-orders">
          <DataTable testId="channel-orders-table" empty="No orders synced yet."
            rows={orders.map((o) => ({ ...o, id: `${o.connection_id}:${o.external_id}` }))}
            columns={[
              { key: "external_id", label: "Order" },
              { key: "connection_id", label: "Channel", render: (o) => nameOf(o.connection_id) },
              { key: "status", label: "Status" },
              { key: "total", label: "Total", right: true, render: (o) => `${o.total ?? "—"} ${o.currency || ""}` },
            ]} />
        </Panel>
        <Panel title="Events" subtitle="These start workflows with a channel-event trigger." className="xl:col-span-3" testId="channel-events">
          <ul className="space-y-1 text-[11px]">
            {events.length === 0 && <li className="text-slate-400">No events yet.</li>}
            {events.map((e) => (
              <li key={e.id} className="text-slate-300">
                <span className="text-slate-500">{when(e.at)}</span> <code>{e.type}</code> {nameOf(e.connection_id)}
                {e.payload.sku ? ` · ${e.payload.sku}` : ""}
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </main>
  );
}
