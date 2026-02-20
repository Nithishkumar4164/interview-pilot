import { Routes, Route, Navigate } from 'react-router-dom';
import { DashboardPage } from '@/pages/DashboardPage';
import { LiveSessionPage } from '@/pages/LiveSessionPage';
import { ReportPage } from '@/pages/ReportPage';

export function App() {
  return (
    <Routes>
      <Route path="/" element={<DashboardPage />} />
      <Route path="/session/:sessionId" element={<LiveSessionPage />} />
      <Route path="/reports/:reportId" element={<ReportPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
