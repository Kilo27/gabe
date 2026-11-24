import { Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import Activity from "./pages/Activity";
import Agents from "./pages/Agents";
import Approvals from "./pages/Approvals";
import Chat from "./pages/Chat";
import InboxPage from "./pages/Inbox";
import Notes from "./pages/Notes";
import SettingsPage from "./pages/Settings";
import Tasks from "./pages/Tasks";
import Today from "./pages/Today";

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Agents />} />
        <Route path="/today" element={<Today />} />
        <Route path="/ask" element={<Chat />} />
        <Route path="/ask/:id" element={<Chat />} />
        <Route path="/approvals" element={<Approvals />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/inbox" element={<InboxPage />} />
        <Route path="/notes" element={<Notes />} />
        <Route path="/activity" element={<Activity />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/settings/:tab" element={<SettingsPage />} />
        <Route path="*" element={<Agents />} />
      </Routes>
    </Shell>
  );
}
