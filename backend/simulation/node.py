"""
node.py — GridNode class representing a single node in the smart grid.

Each node acts as an independent agent with:
- Sensor readings (voltage, frequency, load, generation)
- Hybrid storage (battery + supercapacitor)
- Self-update logic per timestep
"""

import random
import math


class GridNode:
    """
    Represents a single smart grid node (house or substation).

    Hybrid Storage Logic:
      - Supercapacitor: handles short-duration spikes instantly (high power, low energy)
      - Battery:        handles sustained demand periods (lower power, high energy)
    """

    # Real-world power system node types
    # Generation Layer -> Transmission/Distribution -> Load Layer + Storage
    NODE_TYPES = [
        # Generation Layer (connected to substations only)
        "generator_solar",      # Solar farm
        "generator_wind",       # Wind farm
        "generator_nuclear",    # Nuclear plant
        "generator_coal",       # Coal plant
        "generator_gas",        # Gas turbine
        # Transmission/Distribution
        "substation",           # High/Medium voltage transformation
        "pole",                 # Distribution feeder backbone
        "transformer",          # Service transformer
        # Load Layer
        "house",                # Residential
        "hospital",             # Critical load
        "industry",             # Industrial load
        "commercial",           # Commercial building
        # Storage Layer (connected at strategic nodes)
        "battery",              # Grid-scale battery
        "supercap",             # Supercapacitor fast response
    ]

    def __init__(self, node_id: str, node_type: str = "house", x: float = 0.0, y: float = 0.0):
        self.node_id = node_id
        self.node_type = node_type
        
        # Spatial Coordinates (Geographic Mode)
        self.street: str = ""
        self.distance_along: float = 0.0
        self.x = x
        self.y = y

        # --- Electrical parameters ---
        self.voltage: float = 1.0          # per-unit (nominal = 1.0)
        self.frequency: float = 50.0       # Hz (nominal = 50)
        self.load: float = random.uniform(0.3, 0.7)      # MW
        self.generation: float = random.uniform(0.4, 0.8)  # MW
        # Baseline values for time-of-day curve scaling (set once, never drift)
        self._base_load: float = self.load
        self._base_generation: float = self.generation

        # --- Hybrid Storage ---
        self.battery_level: float = 1.0         # 0–1 (State of Charge)
        self.battery_capacity: float = 10.0     # MWh
        self.supercap_level: float = 1.0        # 0–1 (charge fraction)
        self.supercap_capacity: float = 1.0     # MWh (small, fast)

        # --- Status ---
        self.failed: bool = False
        self.isolated: bool = False
        self.stress_level: float = 0.0     # 0–1; used for UI colouring

        # --- Load priority (1=critical/hospital, 2=commercial, 3=residential) ---
        self.priority: int = 2
        self.label: str = ""               # Human-readable name (e.g., "General Hospital")

        # --- History for LSTM input ---
        self.load_history: list = [self.load] * 10
        self.gen_history: list = [self.generation] * 10

        # --- Weather proxy (0=clear, 1=storm) ---
        self.weather: float = 0.0

        # --- Agent coordination ---
        self.excess_energy: float = max(0.0, self.generation - self.load)
        self.deficit: float = max(0.0, self.load - self.generation)

        # --- Node role (used by EMS for dispatch decisions) ---
        # "generation" = solar/wind prosumer, "storage" = primary battery node,
        # "support"    = supercapacitor fast-response, "load" = pure consumer
        self.role: str = "load"

        # --- Energy source type (used by EMS priority dispatch) ---
        # Priority order: solar > wind > battery > coal > nuclear > none
        # "solar"   = photovoltaic (follows SOLAR_CURVE)
        # "wind"    = wind turbine (follows WIND_CURVE, night-active)
        # "nuclear" = flat baseload (never ramps down, always available)
        # "coal"    = conventional thermal (can ramp up/down)
        # "battery" = grid-scale storage (grid BAT0 node)
        # "none"    = load-only, no generation role
        self.source_type: str = "none"

        # --- Cost attributes for PyPSA optimization ---
        # Marginal cost in $/MWh (used for economic dispatch)
        # Defaults: solar=0, wind=0, nuclear=30, coal=50, battery=5, grid=100
        self.marginal_cost: float = 0.0

        # --- Cost attributes for PyPSA optimization ---
        # Marginal cost in $/MWh (for PyPSA EMS optimization)
        # Typical values: solar=0, wind=0, battery=5, coal=50, nuclear=30, grid=60
        self.marginal_cost: float = 0.0  # Default free (renewables)
        if self.source_type == "coal":
            self.marginal_cost = 50.0
        elif self.source_type == "nuclear":
            self.marginal_cost = 30.0
        elif self.source_type == "battery":
            self.marginal_cost = 5.0
        elif self.source_type == "grid":
            self.marginal_cost = 60.0

    # ------------------------------------------------------------------
    # Connection Rules (Real-world power system hierarchy)
    # ------------------------------------------------------------------

    # Allowed connections in power system:
    # GENERATOR -> SUBSTATION
    # SUBSTATION -> POLE/TRANSFORMER
    # POLE -> POLE, POLE -> LOAD, POLE -> STORAGE
    # TRANSFORMER -> POLE, TRANSFORMER -> LOAD

    CAN_CONNECT_TO = {
        "generator_solar": ["substation"],
        "generator_wind": ["substation"],
        "generator_nuclear": ["substation"],
        "generator_coal": ["substation"],
        "generator_gas": ["substation"],
        "substation": ["pole", "transformer", "battery", "supercap"],
        "transformer": ["pole", "house", "hospital", "industry", "commercial"],
        "pole": ["pole", "house", "hospital", "industry", "commercial", "battery", "supercap"],
        "house": ["pole", "transformer"],
        "hospital": ["pole", "transformer"],
        "industry": ["pole", "transformer"],
        "commercial": ["pole", "transformer"],
        "battery": ["substation", "pole", "transformer"],
        "supercap": ["substation", "pole", "transformer"],
    }

    @classmethod
    def can_connect(cls, from_type: str, to_type: str) -> bool:
        """Check if connection from from_type to to_type is allowed."""
        allowed = cls.CAN_CONNECT_TO.get(from_type, [])
        return to_type in allowed

    @classmethod
    def get_connection_layer(cls, node_type: str) -> int:
        """Get the layer level for proper power flow direction."""
        layers = {
            # Layer 0: Generation (source)
            "generator_solar": 0,
            "generator_wind": 0,
            "generator_nuclear": 0,
            "generator_coal": 0,
            "generator_gas": 0,
            # Layer 1: Transmission/Distribution
            "substation": 1,
            # Layer 2: Distribution feeders
            "transformer": 2,
            "pole": 2,
            # Layer 3: Storage (can connect at multiple levels)
            "battery": 3,
            "supercap": 3,
            # Layer 4: Loads (sinks)
            "house": 4,
            "hospital": 4,
            "industry": 4,
            "commercial": 4,
        }
        return layers.get(node_type, 4)

    # ------------------------------------------------------------------
    # Per-timestep update
    # ------------------------------------------------------------------

    def step(self, dt: float = 1.0, timestep: int = 0):
        """
        Advance the node by one simulation timestep.
        Updates physics (voltage, frequency) and prosumer storage logic.
        """
        if self.failed or self.isolated:
            self.voltage = 0.0
            self.frequency = 0.0
            self.load = 0.0
            self.generation = 0.0
            return

        # 1. Base Demand Drift
        load_delta = random.gauss(0, 0.02) + self.weather * 0.05
        self.load = float(max(0.05, min(2.5, self.load + load_delta)))

        # 2. Energy Source Generation Logic
        time_of_day = (timestep % 60) / 60.0

        # Generation logic based on node type
        if self.node_type == "generator_solar":
            # Solar farm generates during day (0.25 to 0.75 of day cycle)
            if 0.25 < time_of_day < 0.75:
                sun_intensity = math.sin((time_of_day - 0.25) * 2 * math.pi)
                solar_gen = self._base_generation * sun_intensity * (1.0 - self.weather) + random.gauss(0, 0.02)
                self.generation = float(max(0.0, solar_gen))
            else:
                self.generation = 0.0

        elif self.node_type == "generator_wind":
            # Wind farm generates more at night (complementary to solar)
            night_intensity = 1.0 - abs(time_of_day - 0.5) * 2
            wind_gen = self._base_generation * (0.3 + 0.7 * night_intensity) * (1.0 + self.weather * 0.2)
            self.generation = float(max(0.0, wind_gen + random.gauss(0, 0.03)))

        elif self.node_type in ["generator_nuclear", "generator_coal", "generator_gas"]:
            # Traditional baseload generators hold steady output + small drift
            gen_delta = random.gauss(0, 0.015) - self.weather * 0.03
            self.generation = float(max(0.0, min(15.0, self.generation + gen_delta)))

        elif self.node_type == "battery":
            # Battery generates based on discharge signal from EMS
            pass  # Generation controlled by EMS dispatch

        elif self.node_type == "supercap":
            # Supercap provides instantaneous power for voltage support
            self.generation = 0.0

        elif self.node_type == "house":
            # Houses have rooftop solar (small prosumer generation)
            if 0.25 < time_of_day < 0.75:
                sun_intensity = math.sin((time_of_day - 0.25) * 2 * math.pi)
                solar_gen = 0.8 * sun_intensity * (1.0 - self.weather) + random.gauss(0, 0.02)
                self.generation = float(max(0.0, solar_gen))
            else:
                self.generation = 0.0

        elif self.node_type in ["hospital", "industry", "commercial"]:
            # Commercial/Industrial buildings may have rooftop solar
            if 0.25 < time_of_day < 0.75:
                sun_intensity = math.sin((time_of_day - 0.25) * 2 * math.pi)
                solar_gen = 0.5 * sun_intensity * (1.0 - self.weather) + random.gauss(0, 0.01)
                self.generation = float(max(0.0, solar_gen))
            else:
                self.generation = 0.0

        else:
            self.generation = 0.0

        # 3. Prosumer Self-Consumption & Storage
        internal_balance = self.generation - self.load
        
        # Charge storage if we have solar surplus
        if internal_balance > 0:
            # Supercap first
            cap_space = 1.0 - self.supercap_level
            cap_charge = min(internal_balance * 0.1 * dt, cap_space)
            self.supercap_level = float(min(1.0, self.supercap_level + cap_charge))
            internal_balance -= cap_charge

            # Then battery
            bat_space = 1.0 - self.battery_level
            bat_charge = min(internal_balance * 0.05 * dt, bat_space)
            self.battery_level = float(min(1.0, self.battery_level + bat_charge))
            internal_balance -= bat_charge

        # 4. Final Grid Exchange (Excess / Deficit after storage)
        self.excess_energy = float(max(0.0, internal_balance))
        self.deficit = float(max(0.0, -internal_balance))

        # 5. Physics state update (Voltage & Frequency based on local strain)
        # Note: True voltage is overwritten later by DC Power Flow if connected.
        # Frequency is kept near nominal 50.0 so we don't start with 100% frequency stress.
        freq_swing = 0.02 if self.node_type == "generator" else 0.1
        self.voltage = float(max(0.9, min(1.1, 1.0 + internal_balance * 0.05)))
        self.frequency = float(max(49.8, min(50.2, 50.0 + internal_balance * freq_swing)))

        # NOTE: stress_level is computed comprehensively by grid._update_stress()
        self.stress_level = 0.0

        # 6. Histories
        self.load_history.append(self.load)
        self.load_history = self.load_history[-10:]  # type: ignore
        self.gen_history.append(self.generation)
        self.gen_history = self.gen_history[-10:]  # type: ignore

    # ------------------------------------------------------------------
    # Hybrid Storage Actions
    # ------------------------------------------------------------------

    def use_supercapacitor(self, amount_mwh: float = 0.1) -> float:
        """
        Draw from supercapacitor to handle a short load spike.
        Returns actual energy delivered.
        """
        available = self.supercap_level * self.supercap_capacity
        delivered = min(amount_mwh, available)
        self.supercap_level = float(max(0.0, self.supercap_level - delivered / self.supercap_capacity))
        # Immediately offsets load on this node
        self.load = float(max(0.0, self.load - delivered))
        return delivered

    def use_battery(self, amount_mwh: float = 0.3) -> float:
        """
        Draw from battery to cover sustained demand.
        Returns actual energy delivered.
        """
        available = self.battery_level * self.battery_capacity
        delivered = min(amount_mwh, available)
        self.battery_level = float(max(0.0, self.battery_level - delivered / self.battery_capacity))
        self.generation = float(min(2.5, self.generation + delivered))
        return delivered

    def increase_generation(self, amount_mw: float = 0.2):
        """Boost generation (e.g., spin up a diesel generator)."""
        self.generation = float(min(2.5, self.generation + amount_mw))

    def shift_load(self, fraction: float = 0.1):
        """Defer / shift a fraction of load to a later timestep."""
        shifted = self.load * fraction
        self.load = float(max(0.0, self.load - shifted))
        return shifted

    # ------------------------------------------------------------------
    # Failure / Recovery
    # ------------------------------------------------------------------

    def fail(self):
        """Mark this node as failed."""
        self.failed = True
        self.voltage = 0.0
        self.frequency = 0.0

    def recover(self):
        """Recover node to a degraded-but-functional state."""
        self.failed = False
        self.isolated = False
        self.voltage = 0.95
        self.frequency = 49.8
        self.load = 0.3
        self.generation = 0.2

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "role": self.role,
            "source_type": self.source_type,
            "voltage": round(float(self.voltage), 4),
            "frequency": round(float(self.frequency), 4),
            "load": round(float(self.load), 4),
            "generation": round(float(self.generation), 4),
            "battery_level": round(float(self.battery_level), 4),
            "supercap_level": round(float(self.supercap_level), 4),
            "failed": self.failed,
            "isolated": self.isolated,
            "x": self.x,
            "y": self.y,
            "stress_level": round(float(self.stress_level), 4),
            "excess_energy": round(float(self.excess_energy), 4),
            "deficit": round(float(self.deficit), 4),
            "weather": round(float(self.weather), 4),
            "priority": self.priority,
            "label": self.label,
        }
