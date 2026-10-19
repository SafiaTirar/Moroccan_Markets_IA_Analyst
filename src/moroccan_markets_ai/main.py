from moroccan_markets_ai.config import settings


def main():
    print("=" * 45)
    print(settings.app_name)
    print("=" * 45)
    print(f"Environment: {settings.app_env}")
    print(f"Data source: {settings.data_source}")
    print("Configuration loaded successfully!")


if __name__ == "__main__":
    main()