import { QueryClientProvider } from '@tanstack/react-query'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { App, createQueryClient } from './App'
import './styles/global.css'

const container = document.getElementById('root')
if (!container) throw new Error('root element missing')

// HashRouter keeps deep links working when the app is loaded from file:// in a
// packaged build.
createRoot(container).render(
  <QueryClientProvider client={createQueryClient()}>
    <HashRouter>
      <App />
    </HashRouter>
  </QueryClientProvider>,
)
