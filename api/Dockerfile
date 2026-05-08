FROM mcr.microsoft.com/dotnet/sdk:9.0 AS build
WORKDIR /src

COPY BizHealthApi.sln .
COPY BizHealthApi/ BizHealthApi/
RUN dotnet restore

RUN dotnet publish BizHealthApi/BizHealthApi.csproj -c Release -o /app/publish --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:9.0 AS final
WORKDIR /app
COPY --from=build /app/publish .
EXPOSE 8080
ENTRYPOINT ["dotnet", "BizHealthApi.dll"]
