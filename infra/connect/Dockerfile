FROM confluentinc/cp-kafka-connect:7.8.0

USER root
ARG NEO4J_CONNECTOR_VERSION=5.5.0
RUN mkdir -p /usr/share/java/neo4j \
    && curl -fSL "https://github.com/neo4j/neo4j-kafka-connector/releases/download/${NEO4J_CONNECTOR_VERSION}/neo4j-kafka-connect-${NEO4J_CONNECTOR_VERSION}.jar" \
      -o "/usr/share/java/neo4j/neo4j-kafka-connect-${NEO4J_CONNECTOR_VERSION}.jar" \
    && chmod 0644 "/usr/share/java/neo4j/neo4j-kafka-connect-${NEO4J_CONNECTOR_VERSION}.jar"
USER appuser

