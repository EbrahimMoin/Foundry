-- Harden blackboard DB so per-sandbox DB roles cannot connect to foundry_blackboard (A7-3)
CREATE EXTENSION IF NOT EXISTS vector;
REVOKE CONNECT ON DATABASE foundry_blackboard FROM PUBLIC;

-- Pre-initialize locked template DB for fast per-sandbox cloning (A7-6)
CREATE DATABASE app_template WITH ALLOW_CONNECTIONS false;
REVOKE CONNECT ON DATABASE app_template FROM PUBLIC;
