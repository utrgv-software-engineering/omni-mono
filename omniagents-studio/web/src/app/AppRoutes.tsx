import { Routes, Route, Navigate } from 'react-router-dom'
import { HomePage } from './pages/HomePage'
import { LogsPage } from './pages/LogsPage'
import { PlaygroundPage } from './pages/PlaygroundPage'
import { AgentsPage } from './pages/AgentsPage'
import { AgentDetailPage } from './pages/AgentDetailPage'
import { EvaluationPage } from './pages/EvaluationPage'

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/logs" element={<LogsPage />} />
      <Route path="/playground" element={<PlaygroundPage />} />
      <Route path="/agents" element={<AgentsPage />} />
      <Route path="/agents/:agentKey" element={<AgentDetailPage />} />
      <Route path="/evaluation" element={<EvaluationPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
