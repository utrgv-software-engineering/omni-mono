# OmniAgents Studio UI

A beautiful, production-ready trace visualization interface for OmniAgents Studio.

## Features

- **Timeline Visualization**: Waterfall chart showing span durations and relationships
- **Real-time Updates**: WebSocket connection for live trace updates
- **Search & Filter**: Full-text search and filtering by span type, errors, etc.
- **Inspector**: Detailed view of span data, errors, and metrics
- **Performance**: Virtualized lists for handling thousands of traces

## Development

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# The UI will be available at http://localhost:5173
# Make sure the OmniAgents Studio server is running on port 7860
```

## Production Build

```bash
# Build for production
npm run build

# Or use the build script from the root directory
cd .. && ./build_ui.sh
```

## Architecture

- **React 18** with TypeScript
- **Vite** for fast development and building
- **TanStack Query** for data fetching and caching
- **Zustand** for state management
- **Tailwind CSS** for styling
- **Radix UI** for accessible components
- **react-window** for virtualized lists

## Components

- `Layout`: Three-panel resizable layout
- `TraceList`: Virtualized list of traces with search
- `Timeline`: Waterfall visualization of spans
- `Inspector`: Detailed span information with tabs

## API Integration

The UI connects to the OmniAgents Studio FastAPI backend at `/api/*` endpoints:
- `GET /api/traces` - List traces
- `GET /api/traces/{id}` - Get trace details
- `GET /api/stats/overview` - Statistics
- `WS /api/ws/traces` - Real-time updates
