-- Harden blackboard DB so per-sandbox DB roles cannot connect to foundry_blackboard (A7-3)
CREATE EXTENSION IF NOT EXISTS vector;
REVOKE CONNECT ON DATABASE foundry_blackboard FROM PUBLIC;
