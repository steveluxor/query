FROM maven:3.9-eclipse-temurin-17 AS builder

WORKDIR /build
COPY pom.xml .
COPY rag-common/pom.xml rag-common/
COPY rag-domain/pom.xml rag-domain/
COPY rag-service/pom.xml rag-service/
COPY rag-api/pom.xml rag-api/

RUN mvn dependency:go-offline -B

COPY rag-common rag-common/
COPY rag-domain rag-domain/
COPY rag-service rag-service/
COPY rag-api rag-api/

RUN mvn clean package -DskipTests -B

FROM eclipse-temurin:17-jre

WORKDIR /app
COPY --from=builder /build/rag-api/target/rag-api-*.jar app.jar

EXPOSE 8085

ENTRYPOINT ["java", "-jar", "app.jar", "--spring.profiles.active=docker"]
