import datetime
from peewee import fn, JOIN, SQL
from backend.models.event_route import EventRoute
from backend.models.route import Route
from backend.models.event import Event
from backend.models.flight import Flight
from backend.models.aircraft import Aircraft
from backend.repositories.real_coverage_repository import RealCoverageRepository
from backend.repositories.coverage_alert_repository import CoverageAlertRepository
import decimal
import math


class CoverageService:
    async def calculate_coverage_for_event(self, event_id, status_filter=None, page=1, limit=10):
        subquery_flights_capacity = (
            Flight.select(
                Flight.ruta_evento,
                fn.SUM(Aircraft.capacidad).alias('total_capacidad_vuelos')
            )
            .join(Aircraft, on=(Flight.aeronave == Aircraft.id))
            .group_by(Flight.ruta_evento)
            .alias('flight_capacity_subquery')
        )

        query = EventRoute.select(
            EventRoute.id,
            EventRoute.demanda_estimada,
            Route.origen,
            Route.destino,
            Event.nombre_evento,
            subquery_flights_capacity.c.total_capacidad_vuelos.alias('capacidad_real_vuelos')
        ).join(
            Route, on=(EventRoute.ruta == Route.id)
        ).join(
            Event, on=(EventRoute.evento == Event.id)
        ).left_outer_join(
            subquery_flights_capacity, on=(EventRoute.id == subquery_flights_capacity.c.ruta_evento_id)

        )

        if event_id is not None:
            query = query.where(EventRoute.evento == event_id)

        all_event_routes_processed = []

        for er_data_row in query.dicts().iterator():
            demanda_estimada = float(er_data_row['demanda_estimada']) if isinstance(er_data_row['demanda_estimada'],
                                                                                    decimal.Decimal) else float(
                er_data_row['demanda_estimada'])
            capacidad_real = float(er_data_row['capacidad_real_vuelos']) if er_data_row[
                                                                                'capacidad_real_vuelos'] is not None else 0.0

            if demanda_estimada > 0:
                porcentaje_cobertura = (capacidad_real / demanda_estimada) * 100
            else:
                porcentaje_cobertura = 100.0

            if porcentaje_cobertura >= 100:
                estado_cobertura = "Cubierta"
            elif porcentaje_cobertura >= 70:
                estado_cobertura = "Parcial"
            else:
                estado_cobertura = "Crítica"

            if status_filter is None or estado_cobertura.lower() == status_filter.lower():
                all_event_routes_processed.append({
                    "id": er_data_row['id'],
                    "nombre_ruta": f"{er_data_row['origen']}-{er_data_row['destino']}",
                    "nombre_evento": er_data_row['nombre_evento'],
                    "demanda_estimada": demanda_estimada,
                    "capacidad_real": capacidad_real,
                    "porcentaje_cobertura": round(porcentaje_cobertura, 2),
                    "estado_cobertura": estado_cobertura,
                    "fecha_calculo": datetime.datetime.now().isoformat()
                })

        total_items_filtered_by_status = len(all_event_routes_processed)

        cubiertas_count = sum(1 for r in all_event_routes_processed if r['estado_cobertura'] == "Cubierta")
        parciales_count = sum(1 for r in all_event_routes_processed if r['estado_cobertura'] == "Parcial")
        criticas_count = sum(1 for r in all_event_routes_processed if r['estado_cobertura'] == "Crítica")

        porcentaje_cubiertas = round((cubiertas_count / total_items_filtered_by_status) * 100,
                                     2) if total_items_filtered_by_status > 0 else 0
        porcentaje_parciales = round((parciales_count / total_items_filtered_by_status) * 100,
                                     2) if total_items_filtered_by_status > 0 else 0
        porcentaje_criticas = round((criticas_count / total_items_filtered_by_status) * 100,
                                    2) if total_items_filtered_by_status > 0 else 0

        summary_metrics = {
            "cubiertas": porcentaje_cubiertas,
            "parciales": porcentaje_parciales,
            "criticas": porcentaje_criticas,
            "total_routes": total_items_filtered_by_status
        }

        start_index = (page - 1) * limit
        end_index = start_index + limit
        paged_routes = all_event_routes_processed[start_index:end_index]

        for er_data in paged_routes:
            original_er = EventRoute.get_or_none(EventRoute.id == er_data['id'])
            if original_er:
                real_coverage = RealCoverageRepository.create(
                    ruta_evento_id=original_er.id,
                    capacidad_real=er_data['capacidad_real'],
                    porcentaje_cobertura=er_data['porcentaje_cobertura'],
                    estado_cobertura=er_data['estado_cobertura'],
                    fecha_calculo=datetime.datetime.now()
                )

                if er_data['estado_cobertura'] == "Crítica":
                    CoverageAlertRepository.create(
                        cobertura_id=real_coverage.id,
                        tipo_alerta="roja",
                        descripcion=f"La cobertura para la ruta '{er_data['nombre_ruta']}' en el evento '{er_data['nombre_evento']}' es CRÍTICA ({er_data['porcentaje_cobertura']:.2f}%). Demanda: {er_data['demanda_estimada']}, Capacidad: {er_data['capacidad_real']}.",
                        fecha_generacion=datetime.datetime.now()
                    )
                elif er_data['estado_cobertura'] == "Parcial":
                    CoverageAlertRepository.create(
                        cobertura_id=real_coverage.id,
                        tipo_alerta="amarilla",
                        descripcion=f"La cobertura para la ruta '{er_data['nombre_ruta']}' en el evento '{er_data['nombre_evento']}' es PARCIAL ({er_data['porcentaje_cobertura']:.2f}%). Demanda: {er_data['demanda_estimada']}, Capacidad: {er_data['capacidad_real']}.",
                        fecha_generacion=datetime.datetime.now()
                    )

        total_pages = math.ceil(total_items_filtered_by_status / limit) if total_items_filtered_by_status > 0 else 1

        return {
            "dashboard_data": paged_routes,
            "summary_metrics": summary_metrics,
            "total_pages": total_pages,
            "total_items": total_items_filtered_by_status
        }

    async def get_route_detail(self, ruta_evento_id):
        event_route = EventRoute.select(
            EventRoute,
            Route,
            Event
        ) \
            .join(Route, JOIN.LEFT_OUTER, on=(EventRoute.ruta == Route.id)) \
            .join(Event, JOIN.LEFT_OUTER, on=(EventRoute.evento == Event.id)) \
            .where(EventRoute.id == ruta_evento_id) \
            .get_or_none()

        if not event_route:
            return None

        demanda_estimada = float(event_route.demanda_estimada) if isinstance(event_route.demanda_estimada,
                                                                             decimal.Decimal) else float(
            event_route.demanda_estimada)

        latest_coverage = RealCoverageRepository.get_latest_for_event_route(ruta_evento_id)
        capacidad_real_total = float(latest_coverage.capacidad_real) if latest_coverage and isinstance(
            latest_coverage.capacidad_real, decimal.Decimal) else (
            latest_coverage.capacidad_real if latest_coverage else 0)
        porcentaje_cobertura_total = float(latest_coverage.porcentaje_cobertura) if latest_coverage and isinstance(
            latest_coverage.porcentaje_cobertura, decimal.Decimal) else (
            latest_coverage.porcentaje_cobertura if latest_coverage else 0.0)
        estado_cobertura_total = latest_coverage.estado_cobertura if latest_coverage else "Sin Datos"

        fecha_inicio_evento = event_route.evento.fecha_inicio
        fecha_fin_evento = event_route.evento.fecha_fin

        if isinstance(fecha_inicio_evento, datetime.date) and not isinstance(fecha_inicio_evento, datetime.datetime):
            fecha_inicio_evento = datetime.datetime.combine(fecha_inicio_evento, datetime.time.min)
        if isinstance(fecha_fin_evento, datetime.date) and not isinstance(fecha_fin_evento, datetime.datetime):
            fecha_fin_evento = datetime.datetime.combine(fecha_fin_evento, datetime.time.max)

        flights_weekly_capacity_query = Flight.select(
            SQL("EXTRACT('dow' FROM fecha_salida) AS day_of_week_num"),
            fn.SUM(Aircraft.capacidad).alias('total_capacity_for_day')
        ).join(Aircraft).where(
            (Flight.ruta_evento == event_route.id) &
            (Flight.fecha_salida >= fecha_inicio_evento) &
            (Flight.fecha_salida <= fecha_fin_evento)
        ).group_by(SQL("EXTRACT('dow' FROM fecha_salida)")).dicts()

        weekly_capacities_map = {
            int(entry['day_of_week_num']): float(entry['total_capacity_for_day'])
            if isinstance(entry['total_capacity_for_day'], decimal.Decimal) else entry['total_capacity_for_day']
            for entry in flights_weekly_capacity_query
        }

        day_names_map = {
            0: "Domingo",
            1: "Lunes",
            2: "Martes",
            3: "Miércoles",
            4: "Jueves",
            5: "Viernes",
            6: "Sábado"
        }

        weekly_coverage_data = []
        for dow_num in range(7):
            day_name = day_names_map[dow_num]
            capacidad_ofrecida_semana_dia = weekly_capacities_map.get(dow_num, 0)

            if demanda_estimada > 0:
                percentage = (capacidad_ofrecida_semana_dia / demanda_estimada) * 100
            else:
                percentage = 0.0

            weekly_coverage_data.append({
                "day": day_name,
                "coverage": round(percentage, 2)
            })

        return {
            "id": event_route.id,
            "route_name": f"{event_route.ruta.origen} - {event_route.ruta.destino}",
            "status": estado_cobertura_total,
            "event_name": event_route.evento.nombre_evento,
            "event_start_date": event_route.evento.fecha_inicio.isoformat(),
            "event_end_date": event_route.evento.fecha_fin.isoformat(),
            "demanda_estimada": demanda_estimada,
            "capacidad_ofrecida": capacidad_real_total,
            "porcentaje_cobertura": porcentaje_cobertura_total,
            "daily_coverage": weekly_coverage_data
        }
