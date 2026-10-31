import { Component, type ReactNode } from 'react'

interface Props {
  /** Label shown in the fallback so the user knows which panel failed. */
  panel: string
  children: ReactNode
}

interface State {
  message: string | null
}

/**
 * Isolates a crashing panel so one bad render cannot blank the whole app.
 * The rest of the UI keeps polling and updating; the failed panel shows the
 * error text and a retry button instead of forcing a full page reload.
 */
export class PanelErrorBoundary extends Component<Props, State> {
  state: State = { message: null }

  static getDerivedStateFromError(error: unknown): State {
    return { message: error instanceof Error ? error.message : String(error) }
  }

  componentDidCatch(error: unknown) {
    // Visible in devtools for diagnosis; the fallback below shows it on screen.
    console.error(`[${this.props.panel}] render failed:`, error)
  }

  render() {
    if (this.state.message !== null) {
      return (
        <div className="card" role="alert" data-testid="panel-error">
          <div className="card-title">
            <span>{this.props.panel} failed to render</span>
          </div>
          <p className="faint" style={{ overflowWrap: 'anywhere' }}>
            {this.state.message}
          </p>
          <button
            type="button"
            className="btn primary"
            onClick={() => this.setState({ message: null })}
          >
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
