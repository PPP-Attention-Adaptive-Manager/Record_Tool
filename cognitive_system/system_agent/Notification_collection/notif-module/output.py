import numpy as np
from features import extract_features
from model import load_model, get_embedding, normalize_features

# Replace these values with the scaler parameters printed
# by Cell 8 of the Colab notebook after training
SCALER_PARAMS = {
    "min_": [0.0, 0.0, 0.0, 0.0, 0.0],
    "max_": [1.0, 1.0, 1.0, 1.0, 1.0],
}


def compute_npi(features):
    npi = (
        features[0] * 0.30
        + features[1] * 0.20
        + features[2] * 0.15
        + features[3] * 0.25
        + features[4] * 0.10
    )
    return float(np.clip(npi, 0.0, 1.0))


def compute_state(npi):
    if npi < 0.25:
        return "Focus"
    elif npi < 0.50:
        return "Normal"
    elif npi < 0.75:
        return "Overloaded"
    else:
        return "Distracted"


def get_output(session):
    raw_features = extract_features()
    npi = compute_npi(raw_features)
    state = compute_state(npi)
    normalized_features = normalize_features(raw_features, SCALER_PARAMS)
    embedding = get_embedding(session, normalized_features)

    return {
        "embedding": embedding,
        "npi": npi,
        "burstiness": float(raw_features[1]),
        "disruption_score": float(raw_features[3]),
        "state": state,
        "metadata": {
            "module": "notifications",
            "embedding_dim": 16,
        },
    }


if __name__ == "__main__":
    import time

    try:
        session = load_model()
        while True:
            output = get_output(session)
            print(f"module:           {output['metadata']['module']}")
            print(f"state:            {output['state']}")
            print(f"npi:              {output['npi']:.4f}")
            print(f"burstiness:       {output['burstiness']:.4f}")
            print(f"disruption_score: {output['disruption_score']:.4f}")
            print(f"embedding:        shape {output['embedding'].shape}")
            print(f"metadata:         {output['metadata']}")
            print()
            time.sleep(5)
    except FileNotFoundError as error:
        print(error)
    except KeyboardInterrupt:
        print("\nStopped.")
