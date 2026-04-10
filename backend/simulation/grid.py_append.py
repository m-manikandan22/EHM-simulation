# NOTE: This file is an append-patch for grid.py methods.
# Imports below resolve IDE static-analysis warnings.
import random
from simulation.node import GridNode  # type: ignore

    # ------------------------------------------------------------------
    # User / UI Dynamic Grid Creation
    # ------------------------------------------------------------------

def add_user_node(self, node_type: str, x: float, y: float) -> dict:
        """Adds a new node from the frontend Add Node mode."""
        
        # Determine prefix and simple ID scheme
        prefix_map = {
            "generator": "G",
            "step_up": "SU",
            "substation": "S",
            "transformer": "T",
            "service": "P",  # Using P for Pole/Service
            "house": "H"
        }
        prefix = prefix_map.get(node_type, "U")
        
        # generate unique ID
        import uuid
        nid = f"{prefix}_{str(uuid.uuid4())[:4]}"
        
        node = GridNode(nid, node_type=node_type, x=x, y=y)
        
        # Base parameters
        if node_type == "generator":
            node.generation = random.uniform(5.0, 8.0)
            node.load = 0.1
        elif node_type == "house":
            node.generation = random.uniform(0.1, 0.4)
            node.load = random.uniform(0.2, 0.8)
        else:
            node.generation = 0.0
            node.load = 0.1
            
        node._base_generation = node.generation
        node._base_load = node.load
        
        self.nodes[nid] = node
        self.graph.add_node(nid)
        
        return node.to_dict()

def add_user_edge(self, u: str, v: str) -> str:
        """Adds a new edge natively enforcing realistic logic constraint."""
        if u not in self.nodes or v not in self.nodes:
            raise ValueError(f"Both nodes {u} and {v} must exist.")
            
        # Call the internal _add_edge which has the constraints logic + distance computation
        self._add_edge(u, v)
        return f"Connected {u} to {v}"

def cut_user_edge(self, u: str, v: str) -> str:
        if self.graph.has_edge(u, v):
            self.graph.remove_edge(u, v)
            return f"Removed link between {u} and {v}"
        raise ValueError(f"No edge exists between {u} and {v}")
