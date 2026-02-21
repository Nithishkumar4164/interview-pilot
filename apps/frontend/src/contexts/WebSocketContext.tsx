/**
 * WebSocket context — provides `send()` to the component tree.
 *
 * Avoids prop-drilling the send function from LiveSessionPage
 * into deeply nested dashboard components.
 */
import { createContext, useContext } from 'react';

interface WebSocketContextValue {
  send: (data: Record<string, unknown>) => void;
}

const WebSocketContext = createContext<WebSocketContextValue>({
  send: () => {},
});

export const WebSocketProvider = WebSocketContext.Provider;

export function useWSSend(): (data: Record<string, unknown>) => void {
  return useContext(WebSocketContext).send;
}
