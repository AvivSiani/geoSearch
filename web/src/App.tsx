import { useState } from 'react';
import { ChatCard } from './components/chat/ChatCard';
import { HistoryPanel } from './components/history/HistoryPanel';
import { MapCard } from './components/map/MapCard';
import { AgentToolsPanel } from './components/tools/AgentToolsPanel';
import { TopBar } from './components/TopBar';
import { useAppState } from './state/AppStateProvider';
import './App.css';

/**
 * Layout (DESIGN_SYSTEM §4). The page is RTL, so in DOM order the history panel
 * renders on the right and the tools panel on the left, as the design fixes.
 */
export function App() {
  const { toolsOpen } = useAppState();
  const [mapExpanded, setMapExpanded] = useState(false);

  return (
    <div className="app">
      <TopBar />
      <main className="workspace">
        <HistoryPanel />
        <div className="center">
          <MapCard expanded={mapExpanded} onToggleExpand={() => setMapExpanded((v) => !v)} />
          <ChatCard hidden={mapExpanded} />
        </div>
        {toolsOpen && <AgentToolsPanel />}
      </main>
    </div>
  );
}
