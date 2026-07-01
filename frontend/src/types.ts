export type Remote = {
  connected: boolean;
  id: string;
  host: string;
  port: number;
  schema: number;
  version: string;
  revision: string;
  last_seen: string;
};

export type Status = {
  dial_frequency?: number;
  mode?: string;
  dx_call?: string;
  report?: string;
  tx_mode?: string;
  tx_enabled?: boolean;
  transmitting?: boolean;
  decoding?: boolean;
  rx_df?: number;
  tx_df?: number;
  de_call?: string;
  de_grid?: string;
  dx_grid?: string;
  sub_mode?: string;
  configuration_name?: string;
  tx_message?: string;
};

export type Decode = {
  index: number;
  id: string;
  received_at: string;
  new: boolean;
  time: string;
  snr?: number;
  delta_time?: number;
  delta_frequency?: number;
  mode: string;
  message: string;
  dxcc_call?: string;
  dxcc_prefix?: string;
  dxcc_entity?: string;
  dxcc_label?: string;
  plugin_color?: string;
  plugin_note?: string;
  worked_call?: boolean;
  worked_call_band?: boolean;
  worked_grid?: boolean;
  worked_grid_band?: boolean;
  worked_dxcc?: boolean;
  worked_dxcc_band?: boolean;
  worked_grid4?: string;
  low_confidence: boolean;
  off_air: boolean;
};

export type Snapshot = {
  remote: Remote;
  server_time?: string;
  status: Status;
  decodes: Decode[];
  transmits?: Decode[];
};

export type DebugEvent = {
  time: string;
  direction: "rx" | "tx";
  remote: string;
  size: number;
  hex: string;
  message: unknown;
  error: string | null;
};

export type WatchedCall = {
  callsign: string;
  grid?: string;
  firstSeen: string;
  lastSeen: string;
  lastDecode?: Decode;
};
