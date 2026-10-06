window.AEGIS_API_BASE_URL = window.AEGIS_API_BASE_URL || (
  window.location.port === '3000'
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : ''
);
