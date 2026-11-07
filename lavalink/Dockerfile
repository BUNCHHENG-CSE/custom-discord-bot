FROM eclipse-temurin:17-jre-jammy

WORKDIR /opt/lavalink

COPY lavalink/Lavalink.jar ./Lavalink.jar
COPY lavalink/plugins/ ./plugins/

RUN mkdir -p logs \
    && chown -R 10001:10001 /opt/lavalink

USER 10001:10001

EXPOSE 2333

CMD ["java", "-Xms128M", "-Xmx768M", "-jar", "Lavalink.jar"]
