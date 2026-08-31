# Magistrate Architecture

Magistrate is not a chatbot or a generic agent framework. It is an **AI-native engineering government**.

## Deskless Operator Alpha boundary

The product names are intentionally separate:

- **Magi** is the human interface: the Expo/React Native iPhone and web
  surfaces for chat, foreground voice, Attention, and PR targets.
- **Magistrate** is the governed execution substrate: the FastAPI Gateway,
  auth/scopes, policy, private Herdr connection, Firstmate, and harnesses.
- **Owner alpha** is a single trusted operator deployment. Its command and
  voice scopes are for the owner only; its app bundle contains no runner,
  provider, bootstrap, or harness credentials.
- **Friend/multi-user product** is future work requiring accounts/invites,
  tenant authorization, isolated execution, and device/session management.
  Friend preview sessions must be restricted to observation/notifications and
  never expose execution credentials.

The native path is `physical iPhone -> HTTPS/WSS Gateway -> private Herdr ->
Firstmate/harnesses`. See [`deskless-operator-alpha.md`](./deskless-operator-alpha.md)
and [`DEAT-001.md`](./DEAT-001.md) for the current slice and its unexecuted
physical acceptance gate.

## The Metaphor

```text
User = President / Commander in Chief
Magistrate = Government / Strategic Command State
Broker = Executive Office
Agent fleets = Naval fleets / departments / field offices
Individual agents = Civil servants, engineers, inspectors
```

## System Architecture

```mermaid
graph TD
    President([President/User]) -->|Directives| ExecOffice[Executive Office]
    
    subgraph "The Government"
        ExecOffice -->|Spawns| FleetCommand[Fleet Command]
        ExecOffice -->|Submits| Congress[Legislative Branch]
        ExecOffice -->|Escalates| Courts[Judicial Branch]
    end
    
    subgraph "Operations Layer"
        FleetCommand -->|Deploys| Ship1[Ship: Backend]
        FleetCommand -->|Deploys| Ship2[Ship: Frontend]
        
        Ship1 -->|Runs| Crew1[Crew Agents]
        Ship2 -->|Runs| Crew2[Crew Agents]
    end
    
    Crew1 -.->|Approval Request| Courts
    Courts -.->|Injunction or Approval| Crew1
```

## Core Abstractions

### 1. The Executive Branch
Powered by a Celery worker pool and Gemini, the Executive Office interprets directives from the President and issues Executive Orders. It manages the lifecycle of Fleets and Ships.

### 2. The Legislative Branch
Creates and stores persistent `Laws` and `Policies` in the PostgreSQL database. These act as hard constraints and system prompts for all spawned agents.

### 3. The Judicial Branch
Controls the execution flow. When a Crew Agent requests to execute a bash command via the `execute_bash_in_sandbox` tool, the Judicial Service opens a `CourtCase`. The case remains blocked until a `Ruling` is issued (either by an automated Security Court or by the President).

### 4. Independent Oversight
The `CensusBureau` and `IntelligenceCommunity` run background tasks to scrape the `EventLog`, calculate metrics, and provide Intelligence Briefs to the President.
